"""Bounded dashboard counters and monotonic acquisition clock."""
import shutil
from pathlib import Path
import time


class RuntimeStatus:
    def __init__(self):
        self.started = None
        self.elapsed = 0.0
        self.files = {'fip': 0, 'das': 0, 'joint': 0}
        self.counters = {}
        self.failures = {}
        self.last_write = None
        self.last_writes = {}
        self.disk_key = None
        self.disk_checked = -10.0
        self.disks = []

    def clock(self, active, now=None):
        now = time.monotonic() if now is None else now
        if active and self.started is None:
            self.started, self.elapsed = now, 0.0
        elif not active and self.started is not None:
            self.elapsed, self.started = max(0, now - self.started), None
        seconds = int(max(0, now - self.started) if self.started is not None else self.elapsed)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f'{hours:02d}:{minutes:02d}:{seconds:02d}'

    def observe(self, kind, session, count, failures=0):
        old_session, old_count = self.counters.get(kind, (None, 0))
        count = max(0, int(count))
        delta = max(0, count - old_count) if old_session == session and count >= old_count else count
        self.counters[kind] = (session, count)
        self.files[kind] += delta
        self.failures[kind] = max(0, int(failures))
        if delta:
            self.last_write = time.monotonic()
            self.last_writes[kind] = self.last_write

    def disk_space(self, paths, now=None):
        now = time.monotonic() if now is None else now
        key = tuple(dict.fromkeys(str(p) for p in paths if str(p).strip()))
        if key == self.disk_key and now - self.disk_checked < 5: return self.disks
        self.disk_key, self.disk_checked = key, now
        disks = {}
        for text in key:
            drive = text
            try:
                path = Path(text).expanduser().absolute()
                drive = path.anchor or str(path)
                if drive in disks: continue
                while not path.exists() and path.parent != path: path = path.parent
                if path.is_file(): path = path.parent
                usage = shutil.disk_usage(path)
                disks[drive] = (drive, usage.free, '')
            except (OSError, ValueError) as exc:
                disks[drive] = (drive, None, str(exc))
        self.disks = list(disks.values())
        return self.disks
