"""Bounded background work. Only drain() invokes UI callbacks."""
from queue import Queue, Empty
import threading


class UiTasks:
    def __init__(self):
        self.owner = threading.get_ident()
        self.closed = threading.Event()
        self.results = Queue()
        self.active = set()

    def submit(self, key, work, success, failure):
        self._assert_owner()
        if self.closed.is_set() or key in self.active:
            return False
        self.active.add(key)

        def run():
            try:
                value, error = work(), None
            except Exception as exc:
                value, error = None, exc
            if not self.closed.is_set():
                self.results.put((key, success, failure, value, error))

        threading.Thread(target=run, daemon=True, name='pv5-'+key).start()
        return True

    def _assert_owner(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('UI callbacks must run on the UI thread')

    def drain(self):
        self._assert_owner()
        if self.closed.is_set():
            return
        for _ in range(16):
            try:
                key, success, failure, value, error = self.results.get_nowait()
            except Empty:
                break
            self.active.discard(key)
            if error is None:
                if success: success(value)
            elif failure:
                failure(error)

    def close(self):
        self._assert_owner()
        self.closed.set()
        self.active.clear()
        while True:
            try:self.results.get_nowait()
            except Empty:break
