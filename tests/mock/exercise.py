#!/usr/bin/env python3
"""Exercise all mock scenarios over real HTTP; also run the Python AuthClient path."""

import argparse
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from server import ALGO_ID, Handler, Model, SCENARIOS, ThreadingHTTPServer, load_crypto


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def request(base, path, method="GET", body=None, timeout=2):
    req = urllib.request.Request(
        base + path, data=body, method=method,
        headers={"Client-ID": "mock-client-1", "User-Agent": "CCTP/android11_64/2104", "Algo-ID": ALGO_ID},
    )
    try:
        with OPENER.open(req, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as response:
        return response.code, response.headers, response.read()


def setup_session(base, cipher):
    status, headers, body = request(base, "/ticket.cgi", "POST", b"00000000-0000-0000-0000-000000000000")
    assert (status, headers["Error-Code"], body.decode()) == (200, "200", ALGO_ID)
    ticket_xml = ("<request><client-id>mock-client-1</client-id><ipv4>192.0.2.10</ipv4>"
                  "<mac>02:00:00:00:00:01</mac></request>")
    status, headers, body = request(base, "/ticket.cgi", "POST", cipher.encrypt(ticket_xml.encode()))
    assert status == 200 and headers["Error-Code"] == "0"
    import xml.etree.ElementTree as ET
    ticket = ET.fromstring(cipher.decrypt(body).decode()).findtext("ticket")
    assert ticket and ticket.startswith("MOCK-")
    auth_xml = f"<request><userid>fake-user</userid><passwd>fake-pass</passwd><ticket>{ticket}</ticket></request>"
    status, headers, body = request(base, "/auth.cgi", "POST", cipher.encrypt(auth_xml.encode()))
    return ticket, (status, headers, body)


def keep(base, cipher, ticket, timeout=2):
    xml = f"<request><ticket>{ticket}</ticket><client-id>mock-client-1</client-id></request>"
    return request(base, "/keep.cgi", "POST", cipher.encrypt(xml.encode()), timeout=timeout)


def term(base, cipher, ticket):
    xml = f"<request><ticket>{ticket}</ticket></request>"
    return request(base, "/term.cgi", "POST", cipher.encrypt(xml.encode()))


def python_client(base, root, model):
    sys.path.insert(0, str(Path(root).expanduser().resolve()))
    from esurface.auth import AuthClient, AuthState
    from esurface.httpclient import CDCContext, HttpClient

    cdc = CDCContext("python-real-client", "00000000-0000-0000-0000-000000000000",
                     "CCTP/android11_64/2104")
    http = HttpClient(cdc)
    http.s.trust_env = False  # Local mock must bypass an operator's shell proxy.
    state = AuthState()
    state.set(user="python-fake")
    client = AuthClient(http, cdc, state, "python-fake", "fake-pass", netcheck=1)
    client._auth_round(base + "/ticket.cgi", base + "/auth.cgi", "192.0.2.10", "192.0.2.1", "")
    assert state.ticket and state.keep_url and state.term_url
    client.keep()
    assert state.count_keep == 1
    assert client.term()
    model.event("python_authclient_ok", ticket=state.ticket)


def exercise(name, root, log_dir):
    if log_dir:
        (log_dir / f"{name}.jsonl").write_text("", encoding="utf-8")
    args = argparse.Namespace(
        scenario=name, seed=1, loss_percent=100 if name == "partial_loss" else 30,
        after=0.16, duration=0.3, delay=0.25, keep_interval=51,
        bandwidth=[("line-a", 125000.0), ("line-b", 125000.0)],
        log_file=str(log_dir / f"{name}.jsonl") if log_dir else None,
    )
    model = Model(args, load_crypto(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.model = model
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        if name == "partial_loss":
            try:
                request(base, "/generate_204")
                raise AssertionError("expected simulated TCP loss")
            except (urllib.error.URLError, ConnectionError):
                pass
        elif name == "slow":
            started = time.monotonic()
            status, _, _ = request(base, "/generate_204")
            assert status == 302 and time.monotonic() - started >= 0.2
        else:
            status, _, _ = request(base, "/generate_204")
            assert status == 302
            ticket, auth = setup_session(base, model.cipher)
            status, headers, body = auth
            if name in ("rate_limit", "bad_credentials", "anti_sharing"):
                expected = {"rate_limit": ("102", "11064000"),
                            "bad_credentials": ("102", "13012000"),
                            "anti_sharing": ("-600", "")}[name]
                assert status == 403 and (headers["Error-Code"], headers.get("Sub-Error", "")) == expected
                if name == "rate_limit":
                    time.sleep(0.34)
                    # A fresh auth request for the same still-valid ticket now succeeds.
                    xml = f"<request><userid>fake-user</userid><passwd>fake-pass</passwd><ticket>{ticket}</ticket></request>"
                    status, _, _ = request(base, "/auth.cgi", "POST", model.cipher.encrypt(xml.encode()))
                    assert status == 200
            else:
                assert status == 200 and b"keep-retry" in model.cipher.decrypt(body)
                if name in ("normal", "portal_redirect_all", "flap"):
                    if name == "flap":
                        model.started = time.monotonic()
                    status, _, _ = request(base, "/generate_204")
                    assert status == (302 if name == "portal_redirect_all" else 204)
                    if name == "flap":
                        time.sleep(0.2)
                        status, _, _ = request(base, "/generate_204")
                        assert status == 302
                    status, _, _ = keep(base, model.cipher, ticket)
                    assert status == 200
                    if name == "normal":
                        python_client(base, root, model)
                        status, _, payload = request(base, "/traffic/line-a?bytes=16384")
                        assert status == 200 and len(payload) == 16384
                        status, _, payload = request(base, "/traffic/line-b?bytes=16384")
                        assert status == 200 and len(payload) == 16384
                        assert term(base, model.cipher, ticket)[0] == 200
                elif name in ("session_expire_after", "periodic_kick"):
                    time.sleep(0.2)
                    status, headers, _ = keep(base, model.cipher, ticket)
                    assert status == 403 and headers["Error-Code"] == "300"
                    assert term(base, model.cipher, ticket)[0] == (302 if name == "periodic_kick" else 200)
                elif name == "keep_500":
                    assert keep(base, model.cipher, ticket)[0] == 500
                elif name == "keep_timeout":
                    try:
                        keep(base, model.cipher, ticket, timeout=0.05)
                        raise AssertionError("expected keep read timeout")
                    except (TimeoutError, socket.timeout, urllib.error.URLError):
                        pass
        model.event("assertion_passed", scenario=name)
        return True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if model.log_file:
            model.log_file.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenario", choices=("all",) + SCENARIOS, default="all")
    p.add_argument("--esurface-root", default="/Users/orpheus/Desktop/天翼校园网/esurface")
    p.add_argument("--log-dir", type=Path)
    args = p.parse_args()
    if args.log_dir:
        args.log_dir.mkdir(parents=True, exist_ok=True)
    for name in SCENARIOS if args.scenario == "all" else (args.scenario,):
        exercise(name, args.esurface_root, args.log_dir)
        print(f"PASS {name}", flush=True)


if __name__ == "__main__":
    main()
