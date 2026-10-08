"""현재 확정된 Mitsubishi PLC 주소와 OPC 논리 태그의 연결표.

비전과 AGV 인터록은 PLC에서 처리하며 Python에는 START만 전달한다. RESET, HOME,
PAUSE 및 숫자 오류 코드는 주소가 확정될 때까지 이 파일에 추가하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlcIoAddress:
    """PLC 주소 하나와 Python 기준 신호 방향을 나타낸다."""

    address: str
    logical_tag: str
    direction: str


PLC_IO_MAP = {
    "Dobot_1": (
        PlcIoAddress("M1001", "command.start", "PLC_TO_PYTHON"),
        PlcIoAddress("M1002", "status.busy", "PYTHON_TO_PLC"),
        PlcIoAddress("M1003", "status.done", "PYTHON_TO_PLC"),
        PlcIoAddress("M1004", "status.error", "PYTHON_TO_PLC"),
        PlcIoAddress("M1005", "status.ready", "PYTHON_TO_PLC"),
    ),
    "Dobot_2": (
        PlcIoAddress("M1011", "command.start", "PLC_TO_PYTHON"),
        PlcIoAddress("M1012", "status.busy", "PYTHON_TO_PLC"),
        PlcIoAddress("M1013", "status.done", "PYTHON_TO_PLC"),
        PlcIoAddress("M1014", "status.error", "PYTHON_TO_PLC"),
        PlcIoAddress("M1015", "status.ready", "PYTHON_TO_PLC"),
        PlcIoAddress("D1011", "input.work_count", "PLC_TO_PYTHON"),
    ),
    "Dobot_3": (
        PlcIoAddress("M1021", "command.start", "PLC_TO_PYTHON"),
        PlcIoAddress("M1022", "status.busy", "PYTHON_TO_PLC"),
        PlcIoAddress("M1023", "status.done", "PYTHON_TO_PLC"),
        PlcIoAddress("M1024", "status.error", "PYTHON_TO_PLC"),
        PlcIoAddress("M1025", "status.ready", "PYTHON_TO_PLC"),
    ),
    "Dobot_4": (
        PlcIoAddress("M1031", "command.start", "PLC_TO_PYTHON"),
        PlcIoAddress("M1032", "status.busy", "PYTHON_TO_PLC"),
        PlcIoAddress("M1033", "status.done", "PYTHON_TO_PLC"),
        PlcIoAddress("M1034", "status.error", "PYTHON_TO_PLC"),
        PlcIoAddress("M1035", "status.ready", "PYTHON_TO_PLC"),
    ),
}

# D1012는 Python 출력이 아니다. PLC가 Dobot_2 FINISH를 확인한 뒤 직접 증가시킨다.
PLC_MANAGED_COUNTERS = {"D1012": "Dobot_2 현재 조립 완료 수량"}


def addresses_for(robot_name: str) -> tuple[PlcIoAddress, ...]:
    """선택 로봇의 확정된 PLC 주소 목록을 반환한다."""
    try:
        return PLC_IO_MAP[robot_name]
    except KeyError as exc:
        raise ValueError(f"등록되지 않은 로봇: {robot_name}") from exc
