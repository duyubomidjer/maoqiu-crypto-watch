"""Bounded, rate-limited error metadata. Never logs exception messages or payloads."""
import logging
from logging.handlers import RotatingFileHandler
import time


class Diagnostics:
    def __init__(self, directory):
        self.directory = directory
        self.last = {}
        self.handler = None

    def record(self, area, error):
        key = (area, type(error).__name__)
        now = time.monotonic()
        if now-self.last.get(key, -1000) < 60:
            return
        self.last[key] = now
        try:
            if self.handler is None:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.handler = RotatingFileHandler(self.directory/"runtime-errors.log", maxBytes=65536,
                                                  backupCount=1, encoding="utf-8")
                self.handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            record = logging.LogRecord("watch", logging.ERROR, "", 0, "%s %s", key, None)
            self.handler.emit(record)
        except (OSError, ValueError):
            pass  # Error reporting cannot break the UI recovery path.

    def close(self):
        if self.handler:
            self.handler.close()
