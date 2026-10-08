"""KEPServerEX OPC-DA 연동 전에 사용할 논리 태그와 핸드셰이크 규칙.

현재는 KEPServer 채널·디바이스·Item ID가 아직 없으므로 실제 서버에 접속하지
않는다. 대신 GUI 모의 신호와 OPC-DA가 나중에 같은 이름을 사용하도록 자료형과
방향을 먼저 고정한다. 실제 Item ID는 별도 매핑 파일에서 이 논리 이름에 연결한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from robot_control.dobot_config import ROBOT_NAMES


class TagDirection(str, Enum):
    """INPUT은 PC가 읽고 OUTPUT은 PC가 KEPServer로 쓰는 값이다."""

    INPUT = "input"
    OUTPUT = "output"


class TagType(str, Enum):
    """PLC와 Python 양쪽에서 공통으로 사용할 최소 자료형이다."""

    BOOL = "bool"
    INT = "int"
    STRING = "string"


@dataclass(frozen=True)
class TagDefinition:
    """논리 태그 하나의 이름, 방향, 자료형과 초깃값."""

    name: str
    direction: TagDirection
    data_type: TagType
    default: bool | int | str
    description: str


# 네 로봇에서 공통으로 읽는 운전 명령이다. PLC가 비전·AGV 조건을 확인한 뒤
# Start를 보내며, Python은 상승 에지(False→True)를 한 번만 소비한다.
COMMON_INPUT_TAGS = (
    TagDefinition("command.start", TagDirection.INPUT, TagType.BOOL, False,
                  "공정 시작 요청(상승 에지 1회 소비)"),
)


# PC가 KEPServer로 돌려주는 현재 확정 상태이다. 별도 Reset 주소는 아직 없으므로
# 실제 FINISH·ERROR 해제 시점은 다음 핸드셰이크 구현 단계에서 PLC 방식과 맞춘다.
COMMON_OUTPUT_TAGS = (
    TagDefinition("status.ready", TagDirection.OUTPUT, TagType.BOOL, False,
                  "HOME 완료 및 새 공정 시작 가능"),
    TagDefinition("status.busy", TagDirection.OUTPUT, TagType.BOOL, False,
                  "HOME 또는 공정 실행 중"),
    TagDefinition("status.done", TagDirection.OUTPUT, TagType.BOOL, False,
                  "요청 공정 정상 완료(Reset까지 유지)"),
    TagDefinition("status.error", TagDirection.OUTPUT, TagType.BOOL, False,
                  "오류 발생(ErrorCode 확인, Reset까지 유지)"),
)


# 설비 특성에 따라 로봇마다 추가로 필요한 입력 신호이다.
ROBOT_INPUT_TAGS = {
    "Dobot_1": (
    ),
    "Dobot_2": (
        TagDefinition("input.work_count", TagDirection.INPUT, TagType.INT, 1,
                      "조립 횟수: 1 또는 2"),
    ),
    "Dobot_3": (
    ),
    "Dobot_4": (
        TagDefinition("input.product_type", TagDirection.INPUT, TagType.INT, 0,
                      "비전4 분류 결과: 2=Lamp_a, 3=Lamp_b, 4=Seat_a, "
                      "5=Seat_b, 6=Main (그 밖의 값은 기동 금지)"),
        TagDefinition("command.base_start", TagDirection.INPUT, TagType.BOOL, False,
                      "차량 하부 창고에서 메인 컨베이어 공급 시작"),
    ),
}


ROBOT_OUTPUT_TAGS = {
    "Dobot_1": (),
    "Dobot_2": (),
    "Dobot_3": (),
    "Dobot_4": (
        TagDefinition("status.base_done", TagDirection.OUTPUT, TagType.BOOL, False,
                      "차량 하부 메인 컨베이어 공급 완료 펄스"),
    ),
}


PRODUCT_TYPE_CODES = {
    2: "Lamp_a",
    3: "Lamp_b",
    4: "Seat_a",
    5: "Seat_b",
    6: "Main",
}


def tags_for(robot_name: str) -> tuple[TagDefinition, ...]:
    """선택 로봇이 사용하는 전체 논리 태그 목록을 반환한다."""
    if robot_name not in ROBOT_NAMES:
        raise ValueError(f"등록되지 않은 로봇: {robot_name}")
    return (
        COMMON_INPUT_TAGS
        + ROBOT_INPUT_TAGS[robot_name]
        + COMMON_OUTPUT_TAGS
        + ROBOT_OUTPUT_TAGS[robot_name]
    )


def validate_value(tag: TagDefinition, value: Any) -> bool | int | str:
    """OPC에서 읽은 값의 자료형을 엄격히 검사하여 오기동을 차단한다."""
    if tag.data_type is TagType.BOOL:
        if not isinstance(value, bool):
            raise ValueError(f"{tag.name} 값은 bool이어야 합니다.")
    elif tag.data_type is TagType.INT:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{tag.name} 값은 int여야 합니다.")
    elif not isinstance(value, str):
        raise ValueError(f"{tag.name} 값은 string이어야 합니다.")
    return value


def logical_item_name(robot_name: str, tag_name: str) -> str:
    """실제 KEP Item ID 매핑 전 사용할 일관된 논리 경로를 만든다."""
    available = {tag.name for tag in tags_for(robot_name)}
    if tag_name not in available:
        raise ValueError(f"{robot_name}에 없는 논리 태그: {tag_name}")
    return f"Dobot.{robot_name}.{tag_name}"
