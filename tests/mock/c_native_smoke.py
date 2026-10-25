#!/usr/bin/env python3
"""Build a temporary native C client with loopback endpoints; run CDC scenarios."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from server import Handler, Model, ThreadingHTTPServer, load_crypto


REPO = Path(__file__).resolve().parents[2]
SCENARIOS = ("normal", "session_expire_after", "rate_limit", "rate_limit_http_200")


def replace_one(path, old, new):
    data = path.read_text()
    if data.count(old) != 1:
        raise RuntimeError(f"expected one patch site in {path}: {old}")
    path.write_text(data.replace(old, new))


def build(temp, port):
    source = REPO / "esurfingclient" / "app"
    app = temp / "app"
    shutil.copytree(source, app)
    probe = app / "src/net/CheckNetwork.c"
    for old, new in (
        ('"http://connectivitycheck.platform.hicloud.com/generate_204"', f'"http://127.0.0.1:{port}/generate_204"'),
        ('"http://connect.rom.miui.com/generate_204"', f'"http://127.0.0.1:{port}/generate_204"'),
        ('"http://14.146.227.141:7001"', f'"http://127.0.0.1:{port}"'),
        ('"http://121.8.177.212:7001"', f'"http://127.0.0.1:{port}"'),
    ):
        replace_one(probe, old, new)
    replace_one(app / "src/dialer/DialerLocation.c",
                '"http://connect.rom.miui.com/generate_204"',
                f'"http://127.0.0.1:{port}/generate_204"')
    replace_one(app / "src/dialer/DialerServer.c", "#define AUTH_SERVER_PORT 7001",
                f"#define AUTH_SERVER_PORT {port}")
    server_src = app / "src/dialer/DialerServer.c"
    data = server_src.read_text()
    for ip in ("14.146.227.141", "121.8.177.212"):
        old = f'    "{ip}",'
        if data.count(old) != 1:
            raise RuntimeError(f"unexpected server list: {ip}")
        data = data.replace(old, '    "127.0.0.1",')
    server_src.write_text(data)

    cmake = (app / "CMakeLists.txt").read_text()
    blocks = [re.search(r"add_executable\(ESurfingClient\s*(.*?)\n\)", cmake, re.S).group(1),
              re.search(r"target_sources\(ESurfingClient PRIVATE\s*(.*?)\n\s*\)", cmake, re.S).group(1)]
    files = [str(app / token) for block in blocks
             for token in re.findall(r"(?:third_party|src)/[A-Za-z0-9_./]+\.c", block)]
    binary = temp / "ESurfingClient"
    command = [os.environ.get("CC", "cc"), "-std=c11", '-DPROGRAM_VERSION="2.2.0"',
               '-DPROGRAM_FULL_VERSION="2.2.0-r7"', f"-I{app / 'include'}",
               f"-I{app / 'src'}", f"-I{app / 'third_party'}", "-o", str(binary),
               *files, "-lcurl", "-lpthread"]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return binary, len(files)


def run_case(binary, temp, model, scenario, evidence_dir):
    cfg = {
        "enabled": True, "log_lv": 4, "log_dir": str(temp),
        "accounts": [{"username": "fake-user", "password": "fake-pass", "channel": 3,
                      "mark": "", "iface": ""}],
        "runtime": {"reconnect": {"intervals_sec": [1], "max_attempts": 3},
                    "probe": {"interval_sec": 1},
                    "keepalive": {"failure_retries": 0}},
    }
    (temp / "ESurfingClient.json").write_text(json.dumps(cfg), encoding="utf-8")
    env = dict(os.environ)
    for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    process = subprocess.Popen([str(binary), "--role", "auth", "--account", "1"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    try:
        output, _ = process.communicate(timeout=7 if scenario.startswith("rate_limit") else 11)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGTERM)
        try:
            output, _ = process.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
    lines = [line for line in output.splitlines()
             if any(word in line for word in ("线路状态", "登录", "心跳", "认证", "KeepRetry", "Error-Code", "Sub-Error"))]
    (evidence_dir / f"c-native-{scenario}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    data = (evidence_dir / f"c-native-{scenario}.jsonl").read_text(encoding="utf-8")
    if scenario == "normal":
        assert '"event": "auth_ok"' in data and '"event": "keep_ok"' in data
        assert "KEEP_OK" in output
    elif scenario == "session_expire_after":
        assert '"event": "session_expired"' in data and '"event": "keep_invalid"' in data
        assert "SESSION_INVALID" in output
    else:
        assert '"event": "auth_rejected"' in data and '"sub_error": "11064000"' in data
        if scenario == "rate_limit_http_200":
            assert '"http_status": 200' in data
        assert "RATE_LIMITED" in output
    assert "线路状态" in output or "登录" in output, output[-1000:]
    model.event("c_native_assertion_passed", scenario=scenario, exit_code=process.returncode)
    print(f"PASS C native {scenario} exit={process.returncode} events={len(data.splitlines())}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--esurface-root", default="/Users/orpheus/Desktop/天翼校园网/esurface")
    p.add_argument("--evidence-dir", type=Path, default=REPO / "tests/mock/evidence")
    p.add_argument("--scenario", choices=("all",) + SCENARIOS, default="all")
    args = p.parse_args()
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="esurf-c-mock-") as tmp:
            temp = Path(tmp)
            binary, count = build(temp, server.server_port)
            print(f"native C build: {count} sources, loopback port {server.server_port}")
            for scenario in SCENARIOS if args.scenario == "all" else (args.scenario,):
                (args.evidence_dir / f"c-native-{scenario}.jsonl").write_text("", encoding="utf-8")
                model_args = argparse.Namespace(
                    scenario="rate_limit" if scenario == "rate_limit_http_200" else scenario,
                    error_http_status=200 if scenario == "rate_limit_http_200" else 403,
                    seed=1, loss_percent=0, after=6, duration=1800,
                    delay=0, keep_interval=76, bandwidth=[],
                    log_file=str(args.evidence_dir / f"c-native-{scenario}.jsonl"),
                )
                model = Model(model_args, load_crypto(args.esurface_root))
                server.model = model
                run_case(binary, temp, model, scenario, args.evidence_dir)
                model.log_file.close()
                time.sleep(0.1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
