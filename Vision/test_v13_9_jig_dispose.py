import asyncio
import threading
import time

from engine import station_loop


class Camera:
    def __init__(self):
        self.seq = 0

    def get(self):
        self.seq += 1
        return (self.seq, time.monotonic(), None, [{"name": "car lamp_R", "score": 0.99}]), None


class IO:
    def __init__(self, stop):
        self.config = {"id": 4, "classes": {}}
        self.stop = stop
        self.status = ""
        self.start_reads = 0
        self.finish_high = False
        self.finished_at = None
        self.dispose_at = None
        self.reset_at = None

    async def prepare(self):
        pass

    async def read(self, key):
        if key == "start":
            self.start_reads += 1
            return self.start_reads != 1
        if key == "discharge_complete":
            if not self.finish_high:
                return False
            if self.dispose_at is None and time.monotonic() - self.finished_at >= 0.25:
                self.dispose_at = time.monotonic()
            return self.dispose_at is not None
        raise AssertionError(key)

    async def put(self, key, value):
        if key == "finish":
            self.finish_high = bool(value)

    async def reset(self):
        if self.finish_high:
            self.reset_at = time.monotonic()
            self.stop.set()
        for key in ("finish", "running", "ng", "error"):
            await self.put(key, False)
        await self.put("result", 0)

    async def finish(self, result, reason):
        await self.put("result", result[1])
        await self.put("ng", result[0] == "NG")
        await self.put("error", result[0] == "ERROR")
        await self.put("running", False)
        await self.put("finish", True)
        self.finished_at = time.monotonic()

    def logger(self, *args):
        pass


async def run():
    stop = threading.Event()
    io = IO(stop)
    common = {
        "max_frame_age": 2,
        "timeout_seconds": 2,
        "decision_confidence": 0.8,
        "stable_frames": 1,
        "result_hold_seconds": 0.05,
    }
    await asyncio.wait_for(station_loop(io, Camera(), common, stop), 2)
    assert io.dispose_at is not None
    assert io.reset_at >= io.dispose_at
    assert io.reset_at - io.finished_at >= 0.24
    print("PASS: V4 결과는 JIG_Dispose ON 전까지 유지되고 ON 이후에만 초기화")


asyncio.run(run())
