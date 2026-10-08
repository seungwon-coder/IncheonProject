"""사용자가 확정한 ROBOT_I/O 태그 목록. 실행/카운터 정책과 분리한다."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RobotOpcUaTag:
    robot: str
    signal: str
    name: str
    data_type: str
    direction: str
    description: str

    @property
    def node_id(self) -> str:
        return f"ns=2;s=M.PLC.ROBOT_I/O.{self.name}"


ROBOT_OPCUA_TAGS = tuple(
    RobotOpcUaTag(f"Dobot_{number}", signal, f"ROBOT_{suffix}_({number})",
                  "Boolean", direction, description)
    for number in range(1, 5)
    for signal, suffix, direction, description in (
        ("command.start", "START", "PLC_TO_PYTHON", "PLC의 공정 기동 명령"),
        ("status.error", "ERROR", "PYTHON_TO_PLC", "Dobot 오류 상태"),
        ("status.done", "FINISH", "PYTHON_TO_PLC", "공정 완료 및 대기 위치 도착"),
        ("status.ready", "READY", "PYTHON_TO_PLC", "대기 위치에서 다음 공정 준비 완료"),
        ("status.busy", "RUNNING", "PYTHON_TO_PLC", "공정 실행 중"),
    )
) + (
    RobotOpcUaTag("Dobot_2", "input.program_no", "ROBOT_Program_NO_(2)",
                  "UInt16", "PLC_TO_PYTHON", "등록된 태그; 현재 Dobot_2 기동 조건에는 사용하지 않음"),
    RobotOpcUaTag("Dobot_2", "input.target_count", "ROBOT2_TARGET_COUNT",
                  "UInt16", "PLC_TO_PYTHON", "조립 목표 횟수 1 또는 2; START 시 이 값으로 공정 선택"),
    RobotOpcUaTag("Dobot_2", "counter.finish_count", "ROBOT2_FINISH_COUNT",
                  "UInt16", "PLC_TO_PYTHON", "PLC가 FINISH마다 증가; 목표 도달 시 TARGET_COUNT와 함께 0으로 초기화"),
    # 이 태그만 ROBOT_I/O가 아니라 VISION_I/O 그룹에 있으므로 node_id 속성을
    # 사용하지 않고 node_map_for_robot()에서 실제 NodeId를 명시한다.
    RobotOpcUaTag("Dobot_4", "input.product_type", "VISION4_CLASS_RESULT",
                  "UInt16", "PLC_TO_PYTHON", "비전4 분류 결과 2~6"),
    RobotOpcUaTag("Dobot_4", "command.base_start", "ROBOT_START(B)_(4)",
                  "Boolean", "PLC_TO_PYTHON", "차량 하부 창고에서 메인 컨베이어 공급 시작"),
    RobotOpcUaTag("Dobot_4", "status.base_done", "ROBOT_FINISH(B)_(4)",
                  "Boolean", "PYTHON_TO_PLC", "차량 하부 메인 컨베이어 공급 완료 펄스"),
)


DOBOT_4_VISION_CLASS_NODE = "ns=2;s=M.PLC.VISION_I/O.VISION4_CLASS_RESULT"


def tags_for_robot(robot: str) -> tuple[RobotOpcUaTag, ...]:
    tags = tuple(tag for tag in ROBOT_OPCUA_TAGS if tag.robot == robot)
    if not tags:
        raise ValueError(f"등록되지 않은 로봇: {robot}")
    return tags


def node_map_for_robot(robot: str) -> dict[str, str]:
    """OpcUaBackend(node_map=...)에 사용할 논리 신호 → NodeId 매핑."""
    mapping = {tag.signal: tag.node_id for tag in tags_for_robot(robot)}
    if robot == "Dobot_4":
        mapping["input.product_type"] = DOBOT_4_VISION_CLASS_NODE
    return mapping
