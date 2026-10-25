#!/usr/bin/env python3
"""Sample the real C auth process against a mock server every five seconds."""

import argparse
import collections
import csv
import ctypes
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mock"))
from c_native_smoke import build  # noqa: E402
from server import Handler, Model, ThreadingHTTPServer, load_crypto  # noqa: E402


def rss_bytes(pid):
    if sys.platform == "linux":
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
        raise RuntimeError("VmRSS absent")
    if sys.platform == "darwin":
        values = (ctypes.c_uint64 * 32)()
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                         ctypes.c_void_p, ctypes.c_int]
        size = libproc.proc_pidinfo(pid, 4, 0, values, ctypes.sizeof(values))
        if size < 16:
            raise RuntimeError(f"proc_pidinfo failed: {size}")
        return int(values[1])  # proc_taskinfo.pti_resident_size
    raise RuntimeError(f"unsupported platform: {platform.system()}")


def sample_one(name, args):
    case_dir = args.output / name
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "mock.jsonl").write_text("")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    model_args = argparse.Namespace(scenario=name, seed=1, loss_percent=0,
                                    after=args.after, duration=1800, delay=0,
                                    keep_interval=76, bandwidth=[],
                                    log_file=str(case_dir / "mock.jsonl"))
    model = Model(model_args, load_crypto(args.esurface_root))
    server.model = model
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    process = None
    with tempfile.TemporaryDirectory(prefix=f"esurf-{name}-") as tmp:
        temp = Path(tmp)
        try:
            binary, sources = build(temp, server.server_port)
            config = {
                "enabled": True, "log_lv": 4, "log_dir": str(temp),
                "accounts": [{"username": "fake-user", "password": "fake-pass",
                              "channel": 3, "mark": "", "iface": ""}],
                "runtime": {"reconnect": {"intervals_sec": [1]},
                            "probe": {"interval_sec": 1},
                            "keepalive": {"failure_retries": 0}},
            }
            (temp / "ESurfingClient.json").write_text(json.dumps(config))
            env = dict(os.environ)
            for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
                env.pop(key, None)
            env["NO_PROXY"] = "*"
            with (case_dir / "client.log").open("w") as client_log, \
                    (case_dir / "rss.csv").open("w", newline="") as csv_file:
                process = subprocess.Popen([str(binary), "--role", "auth", "--account", "1"],
                                           stdout=client_log, stderr=subprocess.STDOUT,
                                           env=env)
                writer = csv.DictWriter(csv_file, fieldnames=("elapsed_s", "unix_s", "rss_bytes",
                    "probe_204", "probe_302", "auth_ok", "keep_ok", "keep_invalid", "session_expired"))
                writer.writeheader()
                counts = collections.Counter()
                offset = 0
                start = time.monotonic()
                due = start
                samples = []
                steady_samples = []
                while True:
                    wait = due - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    elapsed = time.monotonic() - start
                    if process.poll() is not None:
                        raise RuntimeError(f"C client exited early: {process.returncode} at {elapsed:.1f}s")
                    with (case_dir / "mock.jsonl").open() as events:
                        events.seek(offset)
                        while line := events.readline():
                            counts[json.loads(line)["event"]] += 1
                        offset = events.tell()
                    rss = rss_bytes(process.pid)
                    row = {"elapsed_s": round(elapsed, 2), "unix_s": round(time.time(), 3),
                           "rss_bytes": rss}
                    row.update({key: counts[key] for key in writer.fieldnames[3:]})
                    writer.writerow(row)
                    csv_file.flush()
                    samples.append(rss)
                    if elapsed >= 30:
                        steady_samples.append(rss)
                    if elapsed >= args.duration:
                        break
                    due += args.interval
                result = {"scenario": name, "duration_s": round(elapsed, 2),
                          "interval_s": args.interval, "samples": len(samples), "sources": sources,
                          "rss_first_bytes": samples[0], "rss_last_bytes": samples[-1],
                          "rss_min_bytes": min(samples), "rss_max_bytes": max(samples),
                          "rss_delta_bytes": samples[-1] - samples[0],
                          "rss_steady_first_bytes": steady_samples[0] if steady_samples else None,
                          "rss_steady_last_bytes": steady_samples[-1] if steady_samples else None,
                          "rss_steady_delta_bytes": (steady_samples[-1] - steady_samples[0]) if steady_samples else None,
                          "events": dict(counts)}
                (case_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps(result), flush=True)
        finally:
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)
            model.log_file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--esurface-root", default="/Users/orpheus/Desktop/天翼校园网/esurface")
    parser.add_argument("--output", type=Path, default=Path("tests/memory/evidence"))
    parser.add_argument("--scenario", choices=("all", "session_expire_after", "flap"), default="all")
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--after", type=float, default=6)
    args = parser.parse_args()
    if args.duration < 10 or args.interval < 1 or args.after <= 0:
        parser.error("duration >= 10, interval >= 1 and after > 0 required")
    args.output.mkdir(parents=True, exist_ok=True)
    names = ("session_expire_after", "flap") if args.scenario == "all" else (args.scenario,)
    errors = []
    def worker(name):
        try:
            sample_one(name, args)
        except Exception as exc:
            errors.append((name, repr(exc)))
    threads = [threading.Thread(target=worker, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise SystemExit(f"sampling failed: {errors}")


if __name__ == "__main__":
    main()
