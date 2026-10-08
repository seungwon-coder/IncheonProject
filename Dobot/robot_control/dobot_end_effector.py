"""그리퍼와 흡착컵을 같은 방식으로 다루기 위한 입력 검사 도우미."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_ACTIONS = {
    "gripper": {"open", "close", "disable"},
    "suction": {"on", "off", "disable"},
}


@dataclass(frozen=True)
class EndEffectorRequest:
    """검사가 끝난 엔드이펙터 종류와 동작."""

    tool_type: str
    action: str

    @classmethod
    def from_payload(cls, payload: Any) -> "EndEffectorRequest":
        """잘못된 장치 종류나 동작 이름을 실제 출력 전에 차단한다."""
        if not isinstance(payload, dict):
            raise ValueError("엔드이펙터 payload는 딕셔너리여야 합니다.")
        tool_type = str(payload.get("tool_type", "")).lower()
        action = str(payload.get("action", "")).lower()
        if tool_type not in ALLOWED_ACTIONS:
            raise ValueError("엔드이펙터 종류는 gripper 또는 suction이어야 합니다.")
        if action not in ALLOWED_ACTIONS[tool_type]:
            allowed = ", ".join(sorted(ALLOWED_ACTIONS[tool_type]))
            raise ValueError(f"{tool_type} 동작은 {allowed} 중 하나여야 합니다.")
        return cls(tool_type, action)

    @property
    def sdk_values(self) -> tuple[bool, bool]:
        """동작 이름을 SDK의 enableCtrl, on 값으로 변환한다."""
        if self.action == "disable":
            return False, False
        if self.tool_type == "gripper":
            return True, self.action == "close"
        return True, self.action == "on"
