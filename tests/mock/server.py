#!/usr/bin/env python3
"""Local CDC protocol simulator. Test-only; never imported by the product."""

import argparse
import importlib.util
import json
import random
import socketserver
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ALGO_ID = "DEABB8C8-A2BC-48CA-8ED0-8CDF1BD62F61"
SCENARIOS = (
    "normal", "session_expire_after", "portal_redirect_all", "rate_limit",
    "bad_credentials", "anti_sharing", "keep_timeout", "keep_500", "slow",
    "flap", "partial_loss", "periodic_kick",
)


def load_crypto(root):
    path = Path(root).expanduser().resolve() / "esurface" / "crypto.py"
    if not path.is_file():
        raise SystemExit(f"crypto.py not found: {path}; set --esurface-root")
    spec = importlib.util.spec_from_file_location("esurface_mock_crypto", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cipher = module.static_cipher_for_uuid(ALGO_ID)
    if cipher is None:
        raise SystemExit(f"static cipher unavailable: {ALGO_ID}")
    return cipher


class Model:
    def __init__(self, args, cipher):
        self.args = args
        self.cipher = cipher
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.random = random.Random(args.seed)
        self.tickets = {}
        self.sessions = {}
        self.ticket_seq = 0
        self.bw_next = {}
        self.bandwidth = dict(args.bandwidth)
        self.log_file = open(args.log_file, "a", encoding="utf-8") if args.log_file else None

    def event(self, name, **fields):
        row = {"ts": round(time.time(), 3), "scenario": self.args.scenario, "event": name, **fields}
        line = json.dumps(row, ensure_ascii=False, sort_keys=True)
        with self.lock:
            print(line, flush=True)
            if self.log_file:
                self.log_file.write(line + "\n")
                self.log_file.flush()

    def loses(self):
        if self.args.scenario != "partial_loss":
            return False
        with self.lock:
            return self.random.random() * 100 < self.args.loss_percent

    def valid(self, ticket):
        with self.lock:
            session = self.sessions.get(ticket)
            if not session or not session["valid"]:
                return False
            age = time.monotonic() - session["authed_at"]
            if self.args.scenario == "session_expire_after" and age >= self.args.after:
                session["valid"] = False
                self.event("session_expired", ticket=ticket, age=round(age, 2))
                return False
            if self.args.scenario == "periodic_kick" and age >= self.args.after:
                session["valid"] = False
                self.event("periodic_kick", ticket=ticket, age=round(age, 2))
                return False
            return True

    def portal_online(self, client_id, peer_ip):
        with self.lock:
            tickets = [t for t, s in self.sessions.items()
                       if (s["client_id"] == client_id if client_id else s["peer_ip"] == peer_ip)]
        online = any(self.valid(t) for t in tickets)
        if self.args.scenario == "portal_redirect_all":
            return False
        if self.args.scenario == "flap":
            online = online and int((time.monotonic() - self.started) / self.args.after) % 2 == 0
        return online

    def reserve_bandwidth(self, line, size):
        bps = self.bandwidth.get(line)
        if not bps:
            return
        with self.lock:
            now = time.monotonic()
            due = max(now, self.bw_next.get(line, now)) + size / bps
            self.bw_next[line] = due
        time.sleep(max(0, due - time.monotonic()))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def public_base(self):
        return getattr(self.model.args, "public_base", None) or f"http://{self.headers.get('Host', '127.0.0.1')}"

    @property
    def model(self):
        return self.server.model

    def log_message(self, fmt, *args):
        return  # JSON event log is the single request record.

    def reply(self, status, body=b"", headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        self.close_connection = True

    def xml_reply(self, status, xml, error="0", sub_error=""):
        headers = {"Content-Type": "text/xml", "Error-Code": error}
        if sub_error:
            headers["Sub-Error"] = sub_error
        self.reply(status, self.model.cipher.encrypt(xml.encode()), headers)

    def before(self, path):
        if self.model.args.scenario == "slow":
            time.sleep(self.model.args.delay)
        if self.model.loses():
            self.model.event("packet_loss", method=self.command, path=path)
            self.close_connection = True
            try:
                self.connection.shutdown(2)
            except OSError:
                pass
            return False
        return True

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if not self.before(path):
            return
        client_id = self.headers.get("Client-ID", "")
        if path == "/healthz":
            self.reply(200, b"ok\n")
            return
        if path.startswith("/traffic/"):
            self.traffic(path, parsed.query)
            return
        if path == "/portal":
            base = self.public_base()
            ticket = base + "/ticket.cgi?wlanuserip=192.0.2.10&wlanacip=192.0.2.1"
            data = ("<!--//config.campus.js.chinatelecom.com"
                    f"<ticket-url><![CDATA[{ticket}]]></ticket-url>"
                    "//config.campus.js.chinatelecom.com-->").encode()
            self.reply(200, data, {"School Id": "221", "Domain": "mock", "Area": "mock"})
            return
        online = self.model.portal_online(client_id, self.client_address[0])
        if path == "/generate_204" or path == "/":
            if online:
                self.model.event("probe_204", client_id=client_id)
                self.reply(204)
            else:
                base = self.public_base()
                location = base + "/portal?wlanuserip=192.0.2.10&wlanacip=192.0.2.1"
                self.model.event("probe_302", client_id=client_id)
                self.reply(302, headers={"Location": location})
            return
        self.reply(404)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.before(path):
            return
        n = int(self.headers.get("Content-Length", "0"))
        if n > 65536 or n < 0:
            self.reply(413)
            return
        body = self.rfile.read(n)
        client_id = self.headers.get("Client-ID", "")
        if path == "/ticket.cgi" and (body == b"" or body.startswith(b"tarce") or body.startswith(b"00000000")):
            self.model.event("module", client_id=client_id, algo_id=ALGO_ID)
            self.reply(200, ALGO_ID.encode(), {"Error-Code": "200"})
            return
        try:
            xml = ET.fromstring(self.model.cipher.decrypt(body).decode("utf-8"))
        except (ValueError, UnicodeError, ET.ParseError) as exc:
            self.model.event("bad_request", path=path, reason=type(exc).__name__)
            self.reply(400, b"bad encrypted XML")
            return
        value = lambda tag: xml.findtext(tag, default="")
        ticket = value("ticket")
        if path == "/ticket.cgi":
            with self.model.lock:
                self.model.ticket_seq += 1
                ticket = f"MOCK-{self.model.ticket_seq:08d}"
                self.model.tickets[ticket] = client_id
            self.model.event("ticket_issued", client_id=client_id, ticket=ticket)
            self.xml_reply(200, f"<response><ticket>{ticket}</ticket><expire>3600</expire></response>")
        elif path == "/auth.cgi":
            self.auth(client_id, ticket, value("userid"))
        elif path == "/keep.cgi":
            self.keep(client_id, ticket)
        elif path == "/term.cgi":
            was_alive = self.model.valid(ticket)
            with self.model.lock:
                if ticket in self.model.sessions:
                    self.model.sessions[ticket]["valid"] = False
            self.model.event("term", client_id=client_id, ticket=ticket, was_alive=was_alive)
            if self.model.args.scenario == "periodic_kick" and not was_alive:
                self.reply(302, headers={"Location": "/portal"})
            else:
                self.xml_reply(200, "<response><result>ok</result></response>")
        elif path == "/state.cgi":
            alive = self.model.valid(ticket)
            self.model.event("state", client_id=client_id, ticket=ticket, alive=alive)
            self.xml_reply(200, "<response><keep-url>keep.cgi</keep-url></response>" if alive else "<response/>",
                           error="0" if alive else "300")
        else:
            self.reply(404)

    def auth(self, client_id, ticket, user):
        with self.model.lock:
            known = self.model.tickets.get(ticket) == client_id
        if not known:
            self.model.event("auth_rejected", reason="invalid_ticket", client_id=client_id)
            self.xml_reply(403, "<response><error>invalid ticket</error></response>", error="300")
            return
        scenario = self.model.args.scenario
        error, sub_error = "", ""
        if scenario == "rate_limit" and time.monotonic() - self.model.started < self.model.args.duration:
            error, sub_error = "102", "11064000"
        elif scenario == "bad_credentials":
            error, sub_error = "102", "13012000"
        elif scenario == "anti_sharing":
            error = "-600"
        if error:
            http_status = getattr(self.model.args, "error_http_status", 403)
            self.model.event("auth_rejected", client_id=client_id, user=user, error_code=error,
                             sub_error=sub_error, http_status=http_status)
            self.xml_reply(http_status,
                           "<response><error>denied</error></response>", error=error, sub_error=sub_error)
            return
        with self.model.lock:
            self.model.sessions[ticket] = {"client_id": client_id, "peer_ip": self.client_address[0], "user": user,
                                           "authed_at": time.monotonic(), "valid": True}
        base = self.public_base()
        xml = ("<response>"
               f"<keep-url><![CDATA[{base}/keep.cgi]]></keep-url>"
               f"<term-url><![CDATA[{base}/term.cgi]]></term-url>"
               f"<keep-retry>{self.model.args.keep_interval}</keep-retry>"
               "</response>")
        self.model.event("auth_ok", client_id=client_id, user=user, ticket=ticket)
        self.xml_reply(200, xml)

    def keep(self, client_id, ticket):
        scenario = self.model.args.scenario
        if scenario == "keep_timeout":
            self.model.event("keep_timeout", client_id=client_id, ticket=ticket)
            time.sleep(self.model.args.delay)
        if scenario == "keep_500":
            self.model.event("keep_500", client_id=client_id, ticket=ticket)
            self.reply(500, b"mock server failure")
            return
        if not self.model.valid(ticket):
            self.model.event("keep_invalid", client_id=client_id, ticket=ticket, error_code="300")
            self.xml_reply(403, "<response><error>session invalid</error></response>", error="300")
            return
        self.model.event("keep_ok", client_id=client_id, ticket=ticket)
        self.xml_reply(200, f"<response><interval>{self.model.args.keep_interval}</interval></response>")

    def traffic(self, path, query):
        line = path.rsplit("/", 1)[-1]
        if not line or "/" in line:
            self.reply(400)
            return
        params = urllib.parse.parse_qs(query)
        try:
            total = int(params.get("bytes", ["1048576"])[0])
        except ValueError:
            self.reply(400)
            return
        if total < 0 or total > 100_000_000:
            self.reply(400)
            return
        self.model.event("traffic_start", line=line, bytes=total, bytes_per_sec=self.model.bandwidth.get(line))
        self.send_response(200)
        self.send_header("Content-Length", str(total))
        self.send_header("Connection", "close")
        self.end_headers()
        chunk = b"M" * 16384
        sent = 0
        while sent < total:
            part = chunk[:min(len(chunk), total - sent)]
            self.model.reserve_bandwidth(line, len(part))
            try:
                self.wfile.write(part)
            except (BrokenPipeError, ConnectionResetError):
                break
            sent += len(part)
        self.close_connection = True
        self.model.event("traffic_end", line=line, bytes=sent)


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        model = self.server.model
        if model.loses():
            model.event("udp_loss", bytes=len(data))
            return
        sock.sendto(data, self.client_address)
        model.event("udp_echo", bytes=len(data))


def parse_bandwidth(value):
    try:
        line, mbps = value.split("=", 1)
        rate = float(mbps) * 1_000_000 / 8
        if not line or rate <= 0:
            raise ValueError
        return line, rate
    except ValueError:
        raise argparse.ArgumentTypeError("expected LINE=positive_Mbps") from None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenario", choices=SCENARIOS, default="normal")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7001)
    p.add_argument("--public-base", help="absolute base URL returned in portal/auth replies")
    p.add_argument("--udp-port", type=int, default=0, help="optional UDP echo/loss listener")
    p.add_argument("--esurface-root", default="/Users/orpheus/Desktop/天翼校园网/esurface")
    p.add_argument("--after", type=float, default=2, help="expire/flap/kick period in seconds")
    p.add_argument("--duration", type=float, default=1800, help="rate limit duration in seconds")
    p.add_argument("--error-http-status", type=int, choices=(200, 403), default=403,
                   help="HTTP status for auth errors; use 200 to test header classification")
    p.add_argument("--delay", type=float, default=16, help="slow or keep timeout delay in seconds")
    p.add_argument("--loss-percent", type=float, default=30)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--keep-interval", type=int, default=51)
    p.add_argument("--bandwidth", action="append", type=parse_bandwidth, default=[], metavar="LINE=Mbps")
    p.add_argument("--log-file")
    args = p.parse_args(argv)
    if args.after <= 0 or args.duration < 0 or args.delay < 0 or not 0 <= args.loss_percent <= 100:
        p.error("after must be positive; duration/delay nonnegative; loss-percent 0..100")
    model = Model(args, load_crypto(args.esurface_root))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.model = model
    if args.udp_port:
        udp = socketserver.ThreadingUDPServer((args.host, args.udp_port), UDPHandler)
        udp.model = model
        threading.Thread(target=udp.serve_forever, daemon=True).start()
    model.event("listen", host=args.host, port=server.server_port, udp_port=args.udp_port)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if model.log_file:
            model.log_file.close()


if __name__ == "__main__":
    main()
