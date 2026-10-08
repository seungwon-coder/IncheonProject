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
        self.finished_at = None
        self.reset_at = None
        self.finish_high = False

    async def prepare(self):
        pass

    async def read(self, key):
        if key == "discharge_complete":
            return self.finish_high
        assert key == "start"
        self.start_reads += 1
        if self.start_reads == 1:
            return False
        return not self.finish_high

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
    hold = 0.20
    common = {
        "max_frame_age": 2,
        "timeout_seconds": 2,
        "decision_confidence": 0.8,
        "stable_frames": 1,
        "result_pulse_seconds": hold,
    }
    await asyncio.wait_for(station_loop(io, Camera(), common, stop), 2)
    elapsed = io.reset_at - io.finished_at
    assert elapsed >= hold - 0.015, (elapsed, hold)
    print(f"PASS: 결과 신호를 설정된 {elapsed:.3f}초 동안 출력 후 자동 초기화")


asyncio.run(run())
