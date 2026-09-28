"""One-shot owner-authorized restart preserving the process environment in memory."""
import argparse
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess

EXPECTED = Path(r"C:\Users\Solod\AppData\Local\Python\bin\python.exe")
ROOT = Path(r"D:\Codex\New project\ozon_category_dashboard")
ORIGIN = "http://127.0.0.1:8052"
k = c.WinDLL("kernel32", use_last_error=True)
n = c.WinDLL("ntdll")
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
k.CloseHandle.argtypes = [w.HANDLE]
n.NtQueryInformationProcess.argtypes = [w.HANDLE, w.ULONG, c.c_void_p, w.ULONG, c.c_void_p]

def capture(pid):
    handle = k.OpenProcess(0x410, False, pid)
    if not handle:
        raise RuntimeError("Cannot inspect target process")
    def read(address, length):
        buffer = c.create_string_buffer(length)
        count = c.c_size_t()
        if not k.ReadProcessMemory(handle, address, buffer, length, c.byref(count)) or count.value != length:
            raise RuntimeError("Process inspection incomplete; restart refused")
        return buffer.raw
    def ptr(address):
        return int.from_bytes(read(address, 8), "little")
    def unicode_string(address):
        length = int.from_bytes(read(address, 2), "little")
        return read(ptr(address + 8), length).decode("utf-16-le")
    try:
        info = (c.c_void_p * 6)()
        if n.NtQueryInformationProcess(handle, 0, c.byref(info), c.sizeof(info), None):
            raise RuntimeError("Cannot locate process parameters")
        params = ptr(info[1] + 0x20)  # 64-bit Windows PEB/RTL_USER_PROCESS_PARAMETERS.
        cwd = unicode_string(params + 0x38)
        image = unicode_string(params + 0x60)
        command = unicode_string(params + 0x70)
        address = ptr(params + 0x80)
        data = bytearray()
        for offset in range(0, 262144, 2):
            data.extend(read(address + offset, 2))
            if len(data) >= 4 and data[-4:] == b"\0\0\0\0":
                break
        else:
            raise RuntimeError("Environment is not terminated; restart refused")
        env = {}
        for item in data.decode("utf-16-le").split("\0"):
            if item and not item.startswith("="):
                name, value = item.split("=", 1)
                env[name] = value
        if Path(cwd).resolve() != ROOT.resolve() or Path(image).resolve() != EXPECTED.resolve():
            raise RuntimeError("Target path mismatch")
        if command != f'"{EXPECTED}" -u app.py' or env.get("DASHBOARD_PORT") != "8052":
            raise RuntimeError("Target arguments/port mismatch")
        return env
    finally:
        k.CloseHandle(handle)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    env = capture(args.pid)
    origins = [x.strip() for x in env.get("PULSE_ACCESS_ALLOWED_ORIGINS", "").split(",") if x.strip()]
    print({"target_validated": True, "origin_already_allowed": ORIGIN in origins}, flush=True)
    if not args.apply:
        return
    if ORIGIN not in origins:
        origins.append(ORIGIN)
    env["PULSE_ACCESS_ALLOWED_ORIGINS"] = ",".join(origins)
    # Do not persist or print the captured environment or credentials.
    subprocess.run(["taskkill", "/PID", str(args.pid), "/F"], check=True, stdout=subprocess.DEVNULL)
    with open(ROOT / "login_origin_repair.stdout.log", "ab") as stdout, open(ROOT / "login_origin_repair.stderr.log", "ab") as stderr:
        process = subprocess.Popen([str(EXPECTED), "-u", "app.py"], cwd=ROOT, env=env,
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS, close_fds=True)
    print({"replacement_pid": process.pid, "only_environment_change": "PULSE_ACCESS_ALLOWED_ORIGINS"}, flush=True)

if __name__ == "__main__":
    main()
