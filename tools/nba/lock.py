"""One run at a time: a small lock file next to the scripts.

The daily update and the live watcher's after-the-games update can start close together (a PC waking up late,
a run started by hand). Two updates at once once mixed their files in one commit. A run takes the lock by
creating the file; a second run finds it and stops (the update) or waits (publishing). A lock left behind by a
run that crashed or was closed is taken over: its process is gone, or it is older than the run could last.
"""
import ctypes
import datetime as dt
import json
import os
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WIN = sys.platform == "win32"


def _alive(pid):
    """Whether a process with this id is still running on this PC."""
    if pid <= 0:
        return False
    if WIN:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunLock:
    """lock = RunLock("update", stale_after=3 * 3600); if lock.acquire(): ... finally lock.release()"""

    def __init__(self, name, stale_after, folder=HERE):
        self.path = Path(folder) / f".{name}.lock"
        self.stale_after = stale_after
        self.held = False
        self.holder = None  # who holds it, when acquire() fails

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _stale(self, info):
        if info is None:  # unreadable: a run that died while writing it
            return True
        if info.get("host") != socket.gethostname():
            return False  # another PC's lock (a shared folder): only its age can retire it
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(info["started"])).total_seconds()
        except (KeyError, ValueError):
            return True
        return age > self.stale_after or not _alive(int(info.get("pid", 0)))

    def acquire(self, wait=0, poll=5):
        """Take the lock; with wait > 0, keep trying for that many seconds. True if taken."""
        deadline = time.time() + wait
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "host": socket.gethostname(),
                               "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}, f)
                self.held = True
                return True
            except FileExistsError:
                info = self._read()
                if self._stale(info):
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                self.holder = info
                if time.time() >= deadline:
                    return False
                time.sleep(poll)

    def release(self):
        if self.held:
            info = self._read()
            if info and info.get("pid") == os.getpid():
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
            self.held = False

    def describe(self):
        h = self.holder or {}
        try:
            when = dt.datetime.fromisoformat(h["started"]).astimezone().strftime("%H:%M")
        except (KeyError, ValueError):
            when = "an unknown time"
        return f"process {h.get('pid', '?')}, started {when}"
