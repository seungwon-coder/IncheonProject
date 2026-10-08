"""Dobot 4대의 실제 좌표 32개를 KEPServerEX OPC-UA 태그에 기록한다.

이 모듈은 로봇 이동 명령을 보내지 않는다. 각 Worker가 이미 읽어 둔 최신 좌표만
가져와 SCADA가 읽을 수 있는 KEPServerEX Float 태그에 주기적으로 기록한다.
"""

from __future__ import annotations

import math
import threading
from typing import Any, Callable

from communication.opcua_backend import OpcUaBackend
from robot_control.dobot_config import ROBOT_NAMES
from robot_control.robot_position_cache import POSITION_AXES


DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_AGE_SECONDS = 3.0


def position_node_ids() -> dict[str, dict[str, str]]:
    """사용자가 확정한 4대 × 8개 좌표 NodeId를 반환한다."""
    return {
        f"Dobot_{number}": {
            axis: f"ns=2;s=M.PLC.ROBOT_I/O.Coord.ROBOT{number}_{axis.upper()}"
            for axis in POSITION_AXES
        }
        for number in range(1, 5)
    }


POSITION_NODE_IDS = position_node_ids()


def build_position_values(
    workers: dict[str, Any], max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS
) -> tuple[dict[str, float], list[str]]:
    """최신 정상 좌표를 OPC NodeId → float 형식으로 변환한다.

    아직 좌표를 받지 못했거나 오래된 로봇은 이번 전송에서 제외한다. 다른 로봇의
    정상 좌표까지 막지 않도록 누락 로봇 이름을 별도로 돌려준다.
    """
    values: dict[str, float] = {}
    missing: list[str] = []
    for robot_name in ROBOT_NAMES:
        sample = workers[robot_name].latest_position(max_age_seconds)
        if sample is None:
            missing.append(robot_name)
            continue
        for axis in POSITION_AXES:
            value = float(sample[axis])
            if not math.isfinite(value):
                raise ValueError(f"{robot_name}.{axis}: 유효하지 않은 좌표입니다.")
            values[POSITION_NODE_IDS[robot_name][axis]] = value
    return values, missing


class RobotPositionOpcPublisher:
    """최신 좌표를 별도 OPC-UA 연결로 1초마다 전송하는 백그라운드 작업."""

    def __init__(
        self,
        workers: dict[str, Any],
        backend_factory: Callable[[], Any] = OpcUaBackend,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        retry_seconds: float = 5.0,
    ) -> None:
        if interval_seconds < 0.5 or max_age_seconds < 0.5 or retry_seconds < 1.0:
            raise ValueError("좌표 전송 주기·유효시간·재연결 시간 설정이 너무 짧습니다.")
        self.workers = workers
        self.backend_factory = backend_factory
        self.interval_seconds = interval_seconds
        self.max_age_seconds = max_age_seconds
        self.retry_seconds = retry_seconds
        self.stop_requested = threading.Event()
        self.thread: threading.Thread | None = None
        self.status = "대기"
        self.last_error = ""

    @property
    def active(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        if self.active:
            raise RuntimeError("SCADA 좌표 전송이 이미 실행 중입니다.")
        self.stop_requested.clear()
        self.thread = threading.Thread(
            target=self._run, name="dobot-position-opc", daemon=True
        )
        self.thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        self.stop_requested.set()
        if self.thread is not None:
            self.thread.join(timeout)

    def _run(self) -> None:
        backend = None
        while not self.stop_requested.is_set():
            try:
                if backend is None:
                    backend = self.backend_factory()
                    backend.connect()
                    self.status = "KEPServerEX 연결 완료"
                    self.last_error = ""

                values, missing = build_position_values(
                    self.workers, self.max_age_seconds
                )
                if values:
                    backend.write(values)
                sent_robots = len(values) // len(POSITION_AXES)
                if missing:
                    self.status = (
                        f"좌표 전송 {sent_robots}/4대 / 대기: {', '.join(missing)}"
                    )
                else:
                    self.status = "SCADA 좌표 32개 전송 정상"
                self.last_error = ""
                self.stop_requested.wait(self.interval_seconds)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.status = f"SCADA 좌표 전송 오류 - {self.last_error}"
                if backend is not None:
                    try:
                        backend.disconnect()
                    except Exception:
                        pass
                    backend = None
                self.stop_requested.wait(self.retry_seconds)
        if backend is not None:
            try:
                backend.disconnect()
            except Exception:
                pass
        self.status = "SCADA 좌표 전송 중지"
