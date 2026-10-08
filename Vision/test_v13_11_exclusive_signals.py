import asyncio

from vision_core_server import OpcIO


async def collect(station_id, result):
    events = []
    io = OpcIO(None, {"id": station_id}, None, True, lambda *args: None)

    async def put(key, value):
        events.append((key, value))

    io.put = put
    io.state = type("State", (), {"update": lambda self, **kwargs: None})()
    await io.finish(result, "test")
    return events


async def main():
    assert await collect(1, ("OK", 0)) == [
        ("running", False), ("finish", False), ("ng", False), ("error", False), ("finish", True)
    ]
    assert await collect(2, ("NG", 0)) == [
        ("running", False), ("finish", False), ("ng", False), ("error", False), ("ng", True)
    ]
    assert await collect(3, ("ERROR", 0)) == [
        ("running", False), ("finish", False), ("ng", False), ("error", False), ("error", True)
    ]
    assert await collect(4, ("OK", 5)) == [
        ("running", False), ("finish", False), ("ng", False), ("error", False),
        ("result", 5), ("finish", True)
    ]
    assert await collect(4, ("ERROR", 0)) == [
        ("running", False), ("finish", False), ("ng", False), ("error", False),
        ("result", 0), ("error", True)
    ]
    # 코드 1(과거 빈 지그)도 이제 정상 분류로 출력하지 않습니다.
    assert (await collect(4, ("OK", 1)))[-2:] == [("result", 0), ("error", True)]
    print("PASS: exclusive output signal contract with RUNNING OFF first")


asyncio.run(main())
