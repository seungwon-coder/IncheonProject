"""비전4 출력 규칙: 분류값은 유지하고 출력 비트만 펄스 후 OFF합니다."""

import asyncio

from vision_core_server import OpcIO, RuntimeState


async def run():
    io = OpcIO(None, {"id": 4}, RuntimeState(4), True, lambda *args: None)
    events = []

    async def put(key, value):
        events.append((key, value))

    io.put = put

    # 정상 부품: CLASS_RESULT 2~6과 FINISH를 출력합니다.
    await io.finish(("OK", 2), "ROUND")
    assert events[-2:] == [("result", 2), ("finish", True)]

    # 1초 펄스 종료에 해당하는 reset은 비트만 OFF합니다.
    # CLASS_RESULT=0은 PLC가 쓰므로 Python 이벤트에 없어야 합니다.
    await io.reset()
    assert ("result", 0) not in events
    assert events[-4:] == [
        ("finish", False),
        ("running", False),
        ("ng", False),
        ("error", False),
    ]

    # 빈 지그·미검출·색상 미분류·복수 검출은 결과 0과 ERROR입니다.
    events.clear()
    await io.finish(("ERROR", 0), "빈 지그")
    assert events[-2:] == [("result", 0), ("error", True)]

    print("PASS: V4 keeps CLASS_RESULT 2~6; output bits pulse; PLC resets result")


asyncio.run(run())
