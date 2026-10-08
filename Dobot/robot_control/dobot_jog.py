"""Dobot 저속 조그 명령의 안전 범위와 축 방향을 검사하는 도우미.

이 파일은 실제 SDK를 호출하지 않는다. 따라서 로봇을 연결하지 않고도 입력값과
축·방향 변환이 올바른지 자동 테스트할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


AXES = ("x", "y", "z", "r", "j1", "j2", "j3", "j4")
DIRECTIONS = ("+", "-")
MIN_DURATION = 0.02
MAX_DURATION = 0.25
MIN_SPEED_PERCENT = 1.0
MAX_SPEED_PERCENT = 100.0

# Dobot SDK는 직교좌표와 관절축 모두 1~8 명령 번호를 사용한다.
# 실제 모드는 SetJOGCmd의 isJoint 값으로 구분한다.
JOG_COMMAND_CODES = {
    ("x", "+"): 1,
    ("x", "-"): 2,
    ("y", "+"): 3,
    ("y", "-"): 4,
    ("z", "+"): 5,
    ("z", "-"): 6,
    ("r", "+"): 7,
    ("r", "-"): 8,
    ("j1", "+"): 1,
    ("j1", "-"): 2,
    ("j2", "+"): 3,
    ("j2", "-"): 4,
    ("j3", "+"): 5,
    ("j3", "-"): 6,
    ("j4", "+"): 7,
    ("j4", "-"): 8,
}


@dataclass(frozen=True)
class JogRequest:
    """검사가 끝난 조그 조건."""

    axis: str
    direction: str
    duration: float
    speed_percent: float

    @classmethod
    def from_payload(cls, payload: Any) -> "JogRequest":
        """외부 입력을 검사하고 위험한 값은 로봇에 전달하기 전에 거부한다."""
        if not isinstance(payload, dict):
            raise ValueError("조그 payload는 딕셔너리여야 합니다.")
        axis = str(payload.get("axis", "")).lower()
        direction = str(payload.get("direction", ""))
        if axis not in AXES:
            raise ValueError(f"조그 축은 {', '.join(AXES)} 중 하나여야 합니다.")
        if direction not in DIRECTIONS:
            raise ValueError("조그 방향은 + 또는 -여야 합니다.")
        try:
            duration = float(payload.get("duration"))
            speed_percent = float(payload.get("speed_percent"))
        except (TypeError, ValueError) as exc:
            raise ValueError("조그 시간과 속도는 숫자여야 합니다.") from exc
        if not MIN_DURATION <= duration <= MAX_DURATION:
            raise ValueError(
                f"조그 시간은 {MIN_DURATION:.2f}~{MAX_DURATION:.2f}초여야 합니다."
            )
        if not MIN_SPEED_PERCENT <= speed_percent <= MAX_SPEED_PERCENT:
            raise ValueError(
                f"조그 속도는 {MIN_SPEED_PERCENT:.0f}~{MAX_SPEED_PERCENT:.0f}%여야 합니다."
            )
        return cls(axis, direction, duration, speed_percent)

    @property
    def command_code(self) -> int:
        """검사된 축과 방향을 SDK가 사용하는 숫자로 바꾼다."""
        return JOG_COMMAND_CODES[(self.axis, self.direction)]

    @property
    def is_joint(self) -> bool:
        """J1~J4이면 SDK의 관절축 모드를 선택한다."""
        return self.axis.startswith("j")

    def to_payload(self) -> dict[str, Any]:
        """프로세스 사이 Queue로 전달할 단순 딕셔너리를 만든다."""
        return {
            "axis": self.axis,
            "direction": self.direction,
            "duration": self.duration,
            "speed_percent": self.speed_percent,
        }
