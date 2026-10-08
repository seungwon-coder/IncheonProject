"""작은 거리의 저속 PTP 시험 명령을 검사하는 도우미."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MAX_XYZ_DELTA_MM = 5.0
MAX_R_DELTA_DEG = 5.0
MIN_SPEED_PERCENT = 5.0
MAX_SPEED_PERCENT = 100.0
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 120.0
# Worker는 로봇 내부의 도착 확인이 끝난 뒤 결과를 전달할 시간이 더 필요하다.
PTP_RESPONSE_MARGIN_SECONDS = 2.0


@dataclass(frozen=True)
class PTPRequest:
    """검사가 끝난 PTP 목표 좌표와 운전 조건."""

    x: float
    y: float
    z: float
    r: float
    speed_percent: float
    timeout: float
    tolerance: float
    allow_large_move: bool = False

    @classmethod
    def from_payload(cls, payload: Any) -> "PTPRequest":
        """숫자 형식과 저속·시간·허용오차 범위를 검사한다."""
        if not isinstance(payload, dict):
            raise ValueError("PTP payload는 딕셔너리여야 합니다.")
        try:
            values = {key: float(payload.get(key)) for key in ("x", "y", "z", "r")}
            speed = float(payload.get("speed_percent"))
            timeout = float(payload.get("timeout"))
            tolerance = float(payload.get("tolerance", 0.25))
        except (TypeError, ValueError) as exc:
            raise ValueError("PTP 좌표와 운전 조건은 숫자여야 합니다.") from exc
        if not MIN_SPEED_PERCENT <= speed <= MAX_SPEED_PERCENT:
            raise ValueError(f"PTP 속도는 {MIN_SPEED_PERCENT:g}~{MAX_SPEED_PERCENT:g}%여야 합니다.")
        if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
            # 상수를 직접 사용하면 허용 범위를 바꿔도 안내 문구가 함께 바뀐다.
            raise ValueError(
                f"PTP 도착 대기시간은 {MIN_TIMEOUT:.0f}~{MAX_TIMEOUT:.0f}초여야 합니다."
            )
        if not 0.02 <= tolerance <= 0.3:
            raise ValueError("PTP 도착 허용오차는 0.02~0.3이어야 합니다.")
        allow_large_move = payload.get("allow_large_move", False)
        if not isinstance(allow_large_move, bool):
            raise ValueError("대거리 이동 허용 값은 참/거짓이어야 합니다.")
        return cls(
            **values,
            speed_percent=speed,
            timeout=timeout,
            tolerance=tolerance,
            allow_large_move=allow_large_move,
        )

    def validate_small_move(self, current: dict[str, float]) -> None:
        """시험 중 큰 좌표 이동이 입력되면 SDK 명령 전에 차단한다."""
        # 티칭 GUI에서 사용자가 목표 좌표를 직접 확인한 경우에만 거리 제한을 푼다.
        # 기본값은 False이므로 기존 시험 코드와 다른 호출자는 계속 5 mm로 보호된다.
        if self.allow_large_move:
            return
        for axis in ("x", "y", "z"):
            if abs(getattr(self, axis) - current[axis]) > MAX_XYZ_DELTA_MM:
                raise ValueError(f"{axis.upper()} 이동량은 최대 {MAX_XYZ_DELTA_MM:.1f}mm입니다.")
        if abs(self.r - current["r"]) > MAX_R_DELTA_DEG:
            raise ValueError(f"R 이동량은 최대 {MAX_R_DELTA_DEG:.1f}도입니다.")

    def target(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z, "r": self.r}
