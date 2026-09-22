"""Console-free supervisor: runs the experiment loop, restarts it if it exits,
and keeps the PC awake while running. Launched at logon by Task Scheduler via
pythonw.exe, so there's no console window for a stray Ctrl+C or close to kill."""
import ctypes
import msvcrt
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
PYTHON = Path(sys.executable).with_name("python.exe")   # child gets python, not pythonw
CREATE_NO_WINDOW, CREATE_NEW_PROCESS_GROUP = 0x08000000, 0x00000200


def note(msg):
    with open(DATA / "console.log", "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%S}] SUPERVISOR {msg}\n")


def main():
    lock = open(DATA / "supervisor.lock", "w")
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return   # another supervisor is already running
    # ES_CONTINUOUS | ES_SYSTEM_REQUIRED: block sleep while we run
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    while True:
        note("starting experiment loop")
        with open(DATA / "console.log", "ab") as log:
            # stdout is a plain file handle, which Windows would otherwise
            # encode as cp1252 - any non-Latin-1 ticker then raises inside
            # print() and takes the calling step down with it.
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            proc = subprocess.Popen([str(PYTHON), "-u", "run.py", "run"], cwd=ROOT,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env,
                                    creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
            rc = proc.wait()
        note(f"loop exited rc={rc}; restarting in 30s")
        time.sleep(30)


if __name__ == "__main__":
    main()
