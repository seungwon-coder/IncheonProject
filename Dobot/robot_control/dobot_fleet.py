"""Dobot 4대를 서로 독립적으로 관리하는 Fleet(로봇 그룹) 관리자.

Fleet는 한 로봇의 연결 또는 상태 읽기가 실패해도 반복문을 중단하지 않고 나머지
로봇을 계속 처리한다. 따라서 Dobot_1의 오류가 Dobot_2~4의 통신을 함께 중단시키지
않는다. 각 로봇의 성공·실패 결과는 별도로 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from robot_control.dobot_config import DEFAULT_CONFIG, ROBOT_NAMES, load_config
from robot_control.dobot_protocol import CommandName
from robot_control.dobot_worker import DobotWorker


@dataclass(frozen=True)
class RobotResult:
    """로봇 한 대의 작업 결과. 실패해도 다른 로봇 결과와 함께 보관된다."""

    ok: bool
    data: Any = None
    error: str = ""


class DobotFleet:
    """설정에 등록된 Dobot 4대의 Worker 생명주기를 관리한다."""

    def __init__(self, config_path: Path = DEFAULT_CONFIG) -> None:
        self.config = load_config(config_path, require_all_ports=True)
        self.workers: dict[str, DobotWorker] = {}
        for robot_name in ROBOT_NAMES:
            item = self.config["robots"][robot_name]
            self.workers[robot_name] = DobotWorker(
                robot_name,
                item["port"],
                int(item["baudrate"]),
            )

    def connect_all(self, timeout: float = 8.0) -> dict[str, RobotResult]:
        """네 로봇에 각각 연결하며, 한 대가 실패해도 다음 로봇을 계속 연결한다."""
        results: dict[str, RobotResult] = {}
        for robot_name, worker in self.workers.items():
            try:
                results[robot_name] = RobotResult(True, data=worker.start(timeout))
            except Exception as exc:
                results[robot_name] = RobotResult(
                    False, error=f"{type(exc).__name__}: {exc}"
                )
        return results

    def ping_all(self, timeout: float = 5.0) -> dict[str, RobotResult]:
        """모든 Worker의 명령 통신 상태를 개별 결과로 반환한다."""
        return self._read_from_all(CommandName.PING, timeout)

    def read_all_status(self, timeout: float = 5.0) -> dict[str, RobotResult]:
        """오류 Worker는 한 번 복구하고, 나머지 로봇 상태도 계속 읽는다."""
        return self._read_from_all(CommandName.GET_STATUS, timeout)

    def _read_from_all(
        self, command: CommandName, timeout: float
    ) -> dict[str, RobotResult]:
        """읽기 명령을 로봇별 try/except로 격리하여 실행한다."""
        results: dict[str, RobotResult] = {}
        for robot_name, worker in self.workers.items():
            try:
                data = worker.request_with_recovery(
                    command,
                    request_timeout=timeout,
                    connect_timeout=8.0,
                    retries=1,
                )
                results[robot_name] = RobotResult(True, data=data)
            except Exception as exc:
                # 여기서 오류를 저장하고 다음 로봇으로 넘어가는 것이 격리의 핵심이다.
                results[robot_name] = RobotResult(
                    False, error=f"{type(exc).__name__}: {exc}"
                )
        return results

    def terminate_one_for_test(self, robot_name: str) -> None:
        """격리 시험을 위해 지정 Worker만 종료한다. 로봇에는 명령을 보내지 않는다."""
        if robot_name not in self.workers:
            raise KeyError(f"등록되지 않은 로봇: {robot_name}")
        self.workers[robot_name].terminate()

    def close_all(self) -> dict[str, RobotResult]:
        """네 Worker를 각각 종료하며 한 대의 종료 오류가 나머지를 막지 않게 한다."""
        results: dict[str, RobotResult] = {}
        for robot_name, worker in self.workers.items():
            try:
                worker.close()
                results[robot_name] = RobotResult(True, data="closed")
            except Exception as exc:
                results[robot_name] = RobotResult(
                    False, error=f"{type(exc).__name__}: {exc}"
                )
        return results

    def __enter__(self) -> "DobotFleet":
        self.connect_all()
        return self

    def __exit__(self, *_: object) -> None:
        self.close_all()
