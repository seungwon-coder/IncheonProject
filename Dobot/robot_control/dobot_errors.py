"""Dobot 제어 중 발생한 오류를 종류별로 구분하는 공통 예외."""

from __future__ import annotations


# PTP 내부 도착 제한 120초와 결과 전달 여유 2초를 모두 수용한다.
MIN_COMMAND_TIMEOUT = 0.1
MAX_COMMAND_TIMEOUT = 122.0


class DobotCommandError(RuntimeError):
    """Worker가 명령을 처리했지만 안전 검사나 SDK 처리에서 실패한 경우."""


class DobotCommandTimeout(TimeoutError):
    """명령 응답이 정해진 시간 안에 돌아오지 않은 경우."""

    def __init__(self, robot: str, command: str, timeout: float) -> None:
        self.robot = robot
        self.command = command
        self.timeout = timeout
        super().__init__(
            f"{robot}의 {command} 명령이 {timeout:.1f}초 안에 응답하지 않았습니다."
        )


def validate_timeout(timeout: float) -> float:
    """0 또는 지나치게 긴 제한시간이 들어오는 설정 실수를 막는다."""
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("명령 제한시간은 숫자여야 합니다.") from exc
    if not MIN_COMMAND_TIMEOUT <= value <= MAX_COMMAND_TIMEOUT:
        raise ValueError(
            f"명령 제한시간은 {MIN_COMMAND_TIMEOUT:.1f}~"
            f"{MAX_COMMAND_TIMEOUT:.0f}초여야 합니다."
        )
    return value
