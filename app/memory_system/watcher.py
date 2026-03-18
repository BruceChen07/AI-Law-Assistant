from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Dict

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.memory_system.indexer import MemoryIndexer


class _Handler(FileSystemEventHandler):
    def __init__(self, memory_root: Path, debounce_sec: float, callback: Callable[[Path, str], None]):
        self.memory_root = memory_root
        self.debounce_sec = debounce_sec
        self.callback = callback
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _fire(self, path: str, event_name: str) -> None:
        if not str(path).lower().endswith(".md"):
            return
        p = Path(path).resolve()
        if self.memory_root.resolve() not in p.parents and p != self.memory_root.resolve():
            return
        key = str(p)
        now = time.time()
        with self._lock:
            last = self._last.get(key, 0.0)
            if now - last < self.debounce_sec:
                return
            self._last[key] = now
        self.callback(p, event_name)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._fire(event.src_path, "created")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._fire(event.src_path, "modified")

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._fire(event.src_path, "deleted")

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._fire(event.src_path, "deleted")
            self._fire(event.dest_path, "created")


class FileWatcher:
    def __init__(self, memory_root: Path, indexer: MemoryIndexer, debounce_sec: float = 0.6):
        self.memory_root = memory_root
        self.indexer = indexer
        self.debounce_sec = debounce_sec
        self._observer = Observer()

    def _on_event(self, path: Path, event_name: str) -> None:
        if event_name == "deleted":
            self.indexer.remove_file(path)
        else:
            self.indexer.index_file(path)

    def start(self) -> None:
        self.memory_root.mkdir(parents=True, exist_ok=True)
        handler = _Handler(self.memory_root, self.debounce_sec, self._on_event)
        self._observer.schedule(handler, str(self.memory_root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=2.0)
