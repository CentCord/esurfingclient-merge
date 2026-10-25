#!/usr/bin/env python3
"""Control only the local QEMU OpenWrt lab through its dedicated serial FIFO."""

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LAB = Path(os.environ.get("ESURF_VM_LAB", REPO.parent.parent / "lab")).resolve()
FIFO = Path(os.environ.get("ESURF_VM_FIFO", "/tmp/owrt.in"))
CONSOLE = LAB / "console.log"
IMAGE = LAB / "owrt.img"
HTTP_BASE = "http://10.0.2.2:8899"
SERVED = LAB / ".vmctl"


def running_pid():
    if not FIFO.exists():
        return None
    result = subprocess.run(["pgrep", "-fl", "qemu-system-aarch64"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        pid, _, command = line.partition(" ")
        if pid.isdigit() and "owrt.img" in command and "-nographic" in command:
            return int(pid)
    return None


def require_running():
    pid = running_pid()
    if pid is None:
        raise RuntimeError(f"QEMU serial FIFO has no reader: {FIFO}")
    return pid


def write_serial(data):
    require_running()
    fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
    try:
        payload = data.encode("utf-8")
        while payload:
            payload = payload[os.write(fd, payload):]
    finally:
        os.close(fd)


def exec_vm(command, timeout=60, max_output=1024 * 1024):
    if not command.strip():
        raise ValueError("empty VM command")
    token = uuid.uuid4().hex[:12]
    marker_begin = f"__VM_BEGIN_{token}__"
    marker_end = f"__VM_END_{token}__"
    SERVED.mkdir(parents=True, exist_ok=True)
    script = SERVED / f"exec-{token}.sh"
    guest_script = f"/tmp/esurf-vm-{token}.sh"
    script.write_text(f"#!/bin/sh\nprintf '\\n{marker_begin}\\n'\n(\n{command}\n)\n"
                      f"rc=$?\nprintf '\\n{marker_end}:%s\\n' \"$rc\"\n")
    lock_path = LAB / ".vmctl.lock"
    try:
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            require_running()
            offset = CONSOLE.stat().st_size
            url = f"{HTTP_BASE}/.vmctl/{script.name}"
            write_serial(f"wget -qO {guest_script} {url} && sh {guest_script}; rm -f {guest_script}\n")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                with CONSOLE.open("rb") as stream:
                    stream.seek(offset)
                    raw = stream.read(max_output + 16384)
                if len(raw) > max_output + 8192:
                    raise RuntimeError(f"VM output exceeded {max_output} bytes; full serial log: {CONSOLE}")
                output = raw.decode("utf-8", errors="replace").replace("\r", "")
                begin = re.search(rf"(?m)^{re.escape(marker_begin)}$", output)
                end = re.search(rf"(?m)^{re.escape(marker_end)}:(\d+)$", output)
                if begin and end and end.start() > begin.end():
                    body = output[begin.end():end.start()].strip("\n")
                    if len(body.encode()) > max_output:
                        raise RuntimeError(f"VM command output exceeded {max_output} bytes")
                    return int(end.group(1)), body
                time.sleep(0.1)
            raise TimeoutError(f"VM command timed out after {timeout}s; serial tail: {output[-600:]}")
    finally:
        script.unlink(missing_ok=True)


def push_vm(source, target=None):
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    target = target or f"/tmp/{source.name}"
    if not target.startswith("/tmp/"):
        raise ValueError("vm-push target must be under /tmp")
    SERVED.mkdir(parents=True, exist_ok=True)
    name = f"push-{uuid.uuid4().hex[:12]}-{source.name}"
    served = SERVED / name
    shutil.copyfile(source, served)
    try:
        url = f"{HTTP_BASE}/.vmctl/{name}"
        rc, output = exec_vm(f"wget -qO {shlex.quote(target)} {shlex.quote(url)} && "
                             f"test -s {shlex.quote(target)} && wc -c {shlex.quote(target)}")
        if rc:
            raise RuntimeError(f"vm-push failed ({rc}): {output}")
        return target, output
    finally:
        served.unlink(missing_ok=True)


def up_vm(timeout):
    pid = running_pid()
    if pid:
        return pid, "already running"
    if not IMAGE.is_file():
        raise FileNotFoundError(IMAGE)
    if not FIFO.exists():
        os.mkfifo(FIFO)
    firmware = Path("/opt/homebrew/share/qemu/edk2-aarch64-code.fd")
    if not firmware.is_file():
        raise FileNotFoundError(firmware)
    fd = os.open(FIFO, os.O_RDWR | os.O_NONBLOCK)
    start = CONSOLE.stat().st_size if CONSOLE.exists() else 0
    with CONSOLE.open("ab") as console:
        process = subprocess.Popen([
            "/opt/homebrew/bin/qemu-system-aarch64", "-M", "virt", "-cpu", "host",
            "-accel", "hvf", "-m", "1024", "-smp", "2",
            "-drive", f"if=pflash,format=raw,readonly=on,file={firmware}",
            "-drive", f"file={IMAGE},format=raw,if=virtio",
            "-netdev", "user,id=n0", "-device", "virtio-net-pci,netdev=n0", "-nographic",
        ], stdin=fd, stdout=console, stderr=subprocess.STDOUT, cwd=LAB, start_new_session=True)
    os.close(fd)
    (LAB / ".vmctl.pid").write_text(str(process.pid) + "\n")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"QEMU exited during boot: {process.returncode}")
        with CONSOLE.open("rb") as stream:
            stream.seek(start)
            tail = stream.read()[-4096:]
        if b"root@OpenWrt:" in tail:
            return process.pid, "boot prompt ready"
        time.sleep(0.5)
    raise TimeoutError(f"QEMU boot prompt not found after {timeout}s; see {CONSOLE}")


def down_vm(timeout):
    pid = running_pid()
    if pid is None:
        return "already stopped"
    write_serial("poweroff\n")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if running_pid() is None:
            return f"QEMU {pid} stopped"
        time.sleep(0.5)
    raise TimeoutError(f"QEMU {pid} did not power off; no forced kill performed")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    up = sub.add_parser("up"); up.add_argument("--timeout", type=int, default=120)
    down = sub.add_parser("down"); down.add_argument("--timeout", type=int, default=60)
    execute = sub.add_parser("exec"); execute.add_argument("command")
    execute.add_argument("--timeout", type=int, default=60)
    execute.add_argument("--max-output", type=int, default=1024 * 1024)
    push = sub.add_parser("push"); push.add_argument("source"); push.add_argument("target", nargs="?")
    install = sub.add_parser("install"); install.add_argument("package")
    snap = sub.add_parser("snapshot"); snap.add_argument("name")
    restore = sub.add_parser("restore"); restore.add_argument("name")
    args = p.parse_args()
    try:
        if args.action == "status":
            print(json.dumps({"running": running_pid() is not None, "pid": running_pid(),
                              "fifo": str(FIFO), "console": str(CONSOLE), "image": str(IMAGE)}))
        elif args.action == "up":
            print(*up_vm(args.timeout))
        elif args.action == "down":
            print(down_vm(args.timeout))
        elif args.action == "exec":
            rc, output = exec_vm(args.command, args.timeout, args.max_output)
            print(output)
            return rc
        elif args.action == "push":
            target, output = push_vm(args.source, args.target)
            print(f"{target}: {output}")
        elif args.action == "install":
            pkg = Path(args.package).resolve()
            if pkg.suffix not in (".ipk", ".apk"):
                raise ValueError("package must end in .ipk or .apk")
            rc, output = exec_vm("if test -f /etc/config/esurfingclient; then "
                "cp -p /etc/config/esurfingclient /root/esurfingclient.config.bak.vmctl; fi")
            if rc:
                raise RuntimeError(f"config backup failed: {output}")
            guest, _ = push_vm(pkg)
            command = (f"opkg install {shlex.quote(guest)}" if pkg.suffix == ".ipk"
                       else f"apk add --allow-untrusted {shlex.quote(guest)}")
            rc, output = exec_vm(command, timeout=180)
            print(output)
            return rc
        elif args.action in ("snapshot", "restore"):
            if not re.fullmatch(r"[a-zA-Z0-9_.-]+", args.name):
                raise ValueError("snapshot name may contain only letters, digits, _, ., -")
            if running_pid() is not None:
                raise RuntimeError("power off VM before copying its raw image")
            path = LAB / "snapshots" / f"{args.name}.img"
            path.parent.mkdir(parents=True, exist_ok=True)
            source, target = (IMAGE, path) if args.action == "snapshot" else (path, IMAGE)
            if not source.is_file():
                raise FileNotFoundError(source)
            if args.action == "snapshot" and target.exists():
                raise FileExistsError(target)
            subprocess.run(["cp", "-c", str(source), str(target)], check=True)
            print(f"{source} -> {target} ({target.stat().st_size} bytes)")
        return 0
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"vm-{args.action}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
