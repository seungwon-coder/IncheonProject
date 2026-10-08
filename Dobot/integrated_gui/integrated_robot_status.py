"""9단계 통합 GUI에서 실제 Dobot의 연결 상태와 좌표만 읽는 서비스.

이 서비스는 이동 명령을 제공하지 않는다. 따라서 '연결 및 상태 읽기' 버튼으로
실행해도 HOME, JOG, PTP 명령은 전송되지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from robot_control.dobot_config import ROBOT_NAMES
from robot_control.dobot_fleet import DobotFleet, RobotResult


@dataclass
class RobotLiveStatus:
    """GUI 한 칸에 표시할 실제 로봇의 최신 통신 결과."""

    connected: bool = False
    pose: dict[str, float] | None = None
    joints: list[float] | None = None
    alarms: list[int] | None = None
    error: str = ""


class IntegratedRobotStatusService:
    """DobotFleet를 이용해 4대 연결과 읽기 작업을 관리한다."""

    def __init__(self, fleet: DobotFleet | None = None) -> None:
        self.fleet = fleet or DobotFleet()
        self.statuses = {name: RobotLiveStatus() for name in ROBOT_NAMES}
        self.connected = False

    def connect_all(self) -> dict[str, RobotResult]:
        results = self.fleet.connect_all()
        for name, result in results.items():
            status = self.statuses[name]
            status.connected = result.ok
            status.error = "" if result.ok else result.error
        self.connected = any(item.ok for item in results.values())
        return results

    def refresh_all(self) -> dict[str, RobotResult]:
        """좌표와 알람을 읽는다. 읽기 실패는 로봇별로 따로 기록한다."""
        results = self.fleet.read_all_status()
        for name, result in results.items():
            status = self.statuses[name]
            if result.ok:
                data: dict[str, Any] = result.data
                status.connected = True
                status.pose = data.get("pose")
                status.joints = data.get("joints")
                status.alarms = data.get("alarms") or []
                status.error = ""
            else:
                status.connected = False
                status.error = result.error
        self.connected = any(item.connected for item in self.statuses.values())
        return results

    def close_all(self) -> dict[str, RobotResult]:
        results = self.fleet.close_all()
        for status in self.statuses.values():
            status.connected = False
        self.connected = False
        return results

