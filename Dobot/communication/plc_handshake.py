"""PLC START와 Dobot 응답 신호의 안전한 핸드셰이크를 관리한다.

FINISH는 OPC-UA에 ON을 기록한 뒤 일정 시간만 유지하는 완료 펄스이다.
별도 RESET 주소가 없으므로 ERROR는 작업자가 GUI 초기화 또는 HOME 복귀를 성공시킨
경우에만 해제한다.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Mapping, Protocol

from communication.plc_io_map import addresses_for


class PlcOutputWriter(Protocol):
    """향후 실제 OPC-DA 출력 모듈이 구현해야 하는 최소 쓰기 기능."""

    def write_values(self, values: Mapping[str, bool]) -> None:
        """PLC 주소별 BOOL 값을 한 번에 기록한다."""


@dataclass(frozen=True)
class RobotResponseSignals:
    """한 로봇이 PLC에 보내는 네 가지 상태 신호."""

    running: bool = False
    finish: bool = False
    error: bool = False
    ready: bool = False


class RobotPlcHandshake:
    """로봇 한 대의 START 상승 에지와 응답 신호 상태를 보관한다."""

    FINISH_PULSE_SECONDS = 1.0

    def __init__(self, robot_name: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.robot_name = robot_name
        self._clock = clock
        self._lock = threading.RLock()
        self._start_on = False
        self._response = RobotResponseSignals()
        self._finish_published_at: float | None = None

    @property
    def response(self) -> RobotResponseSignals:
        with self._lock:
            if (self._response.finish and self._finish_published_at is not None
                    and self._clock() - self._finish_published_at >= self.FINISH_PULSE_SECONDS):
                self._response = RobotResponseSignals(ready=True)
                self._finish_published_at = None
            return self._response

    def set_start(self, value: bool) -> bool:
        """START의 OFF→ON 순간에만 True를 반환하여 중복 기동을 방지한다."""
        if not isinstance(value, bool):
            raise ValueError("PLC START 값은 bool이어야 합니다.")
        with self._lock:
            rising_edge = value and not self._start_on
            self._start_on = value
            response = self.response
            return rising_edge and response.ready and not response.error and not response.finish

    def mark_ready(self) -> None:
        """HOME 복귀가 끝나 새 START를 받을 수 있음을 표시한다."""
        with self._lock:
            self._response = RobotResponseSignals(ready=True)
            self._finish_published_at = None

    def mark_running(self) -> None:
        """START를 받아 공정을 실행하는 동안 RUNNING만 ON으로 만든다."""
        with self._lock:
            self._response = RobotResponseSignals(running=True)
            self._finish_published_at = None

    def mark_finished(self) -> None:
        """공정과 HOME 복귀가 끝났음을 FINISH와 READY로 알린다."""
        with self._lock:
            self._response = RobotResponseSignals(finish=True, ready=True)
            # 첫 OPC-UA ON 쓰기가 성공하기 전에는 펄스 시간을 차감하지 않는다.
            self._finish_published_at = None

    def mark_finish_published(self) -> None:
        """FINISH ON이 전송된 첫 순간부터 펄스 종료 시간을 계산한다."""
        with self._lock:
            if self._response.finish and self._finish_published_at is None:
                self._finish_published_at = self._clock()

    def mark_error(self) -> None:
        """공정 오류를 유지하고 READY와 RUNNING을 내린다."""
        with self._lock:
            self._response = RobotResponseSignals(error=True)
            self._finish_published_at = None

    def mark_disconnected(self) -> None:
        """통신 해제 시 모든 PLC 출력 상태를 안전하게 OFF로 만든다."""
        with self._lock:
            self._start_on = False
            self._response = RobotResponseSignals()
            self._finish_published_at = None

    def output_by_address(self) -> dict[str, bool]:
        """논리 상태를 M1002 같은 실제 PLC 주소로 변환한다."""
        response = self.response
        logical_values = {
            "status.busy": response.running,
            "status.done": response.finish,
            "status.error": response.error,
            "status.ready": response.ready,
        }
        return {
            item.address: logical_values[item.logical_tag]
            for item in addresses_for(self.robot_name)
            if item.direction == "PYTHON_TO_PLC"
        }

    def publish(self, writer: PlcOutputWriter) -> None:
        """현재 네 상태를 실제 또는 시험용 PLC Writer로 전달한다."""
        response = self.response
        writer.write_values(self.output_by_address())
        if response.finish:
            self.mark_finish_published()
