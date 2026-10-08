"""PLC·비전·AGV 신호를 Dobot 공정 명령으로 바꾸는 상태 머신.

이 파일은 신호가 어디서 왔는지 알 필요가 없다. 현재는 다음 8단계의 GUI 모의
신호를 받을 수 있고, 나중에는 같은 ProcessSignals 형식에 OPC-DA 값을 넣으면 된다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from robot_control.robot_motion_controller import RobotMotionController


class ProcessState(str, Enum):
    """GUI와 로그에서 공통으로 사용할 로봇 공정 상태."""

    DISCONNECTED = "disconnected"
    HOMING = "homing"
    WAITING = "waiting"
    WAITING_SECOND_SUPPLY = "waiting_second_supply"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass(frozen=True)
class ProcessSignals:
    """PLC·비전·AGV에서 들어올 신호를 한 묶음으로 표현한다."""

    # 실제 운전에서는 PLC가 비전·AGV 조건을 모두 확인한 뒤 START만 보낸다.
    start: bool = False
    supply_complete: bool = False
    vision_ok: bool = False
    agv_supply_complete: bool = False
    product_arrived: bool = False
    work_count: int = 1
    product_type: str | None = None
    completed_count: int | None = None

    def __post_init__(self) -> None:
        if self.work_count not in (1, 2):
            raise ValueError("Dobot_2 작업 횟수는 1 또는 2여야 합니다.")
        if self.completed_count is not None and (
            type(self.completed_count) is not int
            or not 0 <= self.completed_count < self.work_count
        ):
            raise ValueError("완료 횟수는 0 이상 목표 횟수 미만이어야 합니다.")


class ProcessStateError(RuntimeError):
    """현재 상태나 입력 신호 때문에 공정을 실행할 수 없을 때 발생한다."""


DOBOT_4_DESTINATIONS = {
    # Main은 실제 운전에서 재고 관리기가 1st~3rd 중 빈 위치로 다시 선택한다.
    # 아래 1st 값은 장비 없는 GUI 경로 검사에서만 사용하는 기본 목적지이다.
    "Main": "storage_base_1st",
    "Lamp_a": "return_Lamp_a",
    "Lamp_b": "return_Lamp_b",
    "Seat_a": "return_Seat_a",
    "Seat_b": "return_Seat_b",
}

# 재고 관리기가 선택하는 내부 적층 위치이다. 일반 제품 선택 목록에는 노출하지 않는다.
DOBOT_4_STACK_DESTINATIONS = {
    "Main_1st": "storage_base_1st",
    "Main_2nd": "storage_base_2nd",
    "Main_3rd": "storage_base_3rd",
}


class RobotProcessStateMachine:
    """로봇 한 대의 공정 상태와 기동 조건을 관리한다."""

    def __init__(self, robot_name: str, controller: RobotMotionController | Any) -> None:
        if robot_name not in {"Dobot_1", "Dobot_2", "Dobot_3", "Dobot_4"}:
            raise ValueError(f"등록되지 않은 로봇: {robot_name}")
        self.robot_name = robot_name
        self.controller = controller
        self.state = ProcessState.DISCONNECTED
        self.last_error = ""
        self._initialized = False
        self._lock = threading.RLock()

    def initialize(self, mechanical_home: bool = True) -> list[dict[str, Any]]:
        """연결된 Worker를 HOME으로 이동한 뒤 첫 공정 대기 상태로 만든다."""
        with self._lock:
            if self.state is not ProcessState.DISCONNECTED:
                raise ProcessStateError("연결 해제 상태에서만 HOME 초기화를 시작할 수 있습니다.")
            self.state = ProcessState.HOMING
        try:
            result = self.controller.initialize(mechanical_home=mechanical_home)
        except Exception as exc:
            self._set_error(exc)
            raise
        with self._lock:
            self._initialized = True
            self.state = ProcessState.WAITING
        return result

    def process(
        self, signals: ProcessSignals, *, on_start: Callable[[], None] | None = None
    ) -> list[dict[str, Any]] | None:
        """현재 신호가 기동 조건을 만족하면 해당 로봇 공정을 한 단계 실행한다."""
        with self._lock:
            if self.state not in {
                ProcessState.WAITING,
                ProcessState.WAITING_SECOND_SUPPLY,
            }:
                raise ProcessStateError(f"현재 {self.state.value} 상태에서는 기동할 수 없습니다.")
            destination = self._destination_for(signals)
            if destination is None:
                return None
            self.state = ProcessState.RUNNING
        try:
            if on_start is not None:
                on_start()
            result = self.controller.run_cycle(destination)
        except Exception as exc:
            self._set_error(exc)
            raise
        with self._lock:
            if self.robot_name == "Dobot_2" and destination == "assembly_1" \
                    and signals.work_count == 2:
                self.state = ProcessState.WAITING_SECOND_SUPPLY
            else:
                self.state = ProcessState.WAITING
        return result

    def process_dobot_4_base_supply(
        self, storage_point: str, *, on_start: Callable[[], None] | None = None
    ) -> list[dict[str, Any]]:
        """Dobot_4 창고 출고 사이클을 일반 제품 입고와 같은 상태 잠금으로 실행한다."""
        with self._lock:
            if self.robot_name != "Dobot_4":
                raise ProcessStateError("베이스 공급 사이클은 Dobot_4만 실행할 수 있습니다.")
            if self.state is not ProcessState.WAITING:
                raise ProcessStateError(f"현재 {self.state.value} 상태에서는 기동할 수 없습니다.")
            self.state = ProcessState.RUNNING
        try:
            if on_start is not None:
                on_start()
            result = self.controller.run_base_supply_cycle(storage_point)
        except Exception as exc:
            self._set_error(exc)
            raise
        with self._lock:
            self.state = ProcessState.WAITING
        return result

    def accept_verified_ready(self, current_pose: dict[str, float], tolerance: float = 0.5) -> None:
        """별도 프로그램에서 확인한 사용자 READY를 안전하게 인계한다."""
        with self._lock:
            if self.state is not ProcessState.DISCONNECTED:
                raise ProcessStateError("연결 해제 상태에서만 HOME 좌표를 인계할 수 있습니다.")
        self.controller.confirm_ready_pose(current_pose, tolerance)
        with self._lock:
            self._initialized = True
            self.state = ProcessState.WAITING

    def accept_operator_ready(self) -> None:
        """작업자 승인으로 HOME·좌표 검사 없이 공정 대기 상태를 인계한다."""
        with self._lock:
            if self.state not in {
                ProcessState.DISCONNECTED, ProcessState.WAITING,
                ProcessState.WAITING_SECOND_SUPPLY,
            }:
                raise ProcessStateError(
                    f"현재 {self.state.value} 상태에서는 READY를 승인할 수 없습니다."
                )
            self.controller.accept_operator_ready()
            self._initialized = True
            if self.state is ProcessState.DISCONNECTED:
                self.state = ProcessState.WAITING

    def accept_verified_waiting_pose(
        self, point_name: str, current_pose: dict[str, float], tolerance: float = 0.5
    ) -> None:
        """이전 사이클의 대기 위치를 좌표로 검증해 새 프로그램에 인계한다."""
        with self._lock:
            if self.state is not ProcessState.DISCONNECTED:
                raise ProcessStateError("연결 해제 상태에서만 대기 좌표를 인계할 수 있습니다.")
        self.controller.confirm_waiting_pose(point_name, current_pose, tolerance)
        with self._lock:
            self._initialized = True
            self.state = ProcessState.WAITING

    def _destination_for(self, signals: ProcessSignals) -> str | None:
        """로봇별 인터록을 검사하고 실행할 티칭 목적지를 선택한다."""
        # 최종 설비에서는 PLC가 외부 인터록을 처리한다. 기존 세부 신호 조합은
        # 서버가 없는 GUI 모의 시험과 이전 검사 코드의 호환을 위해서만 허용한다.
        if self.robot_name == "Dobot_1":
            legacy_start = signals.supply_complete and signals.vision_ok
            return "assembly_1" if signals.start or legacy_start else None
        if self.robot_name == "Dobot_2":
            legacy_start = signals.supply_complete and signals.vision_ok
            if not (signals.start or legacy_start):
                return None
            if signals.completed_count is not None:
                return "assembly_1" if signals.completed_count == 0 else "assembly_2"
            return (
                "assembly_2"
                if self.state is ProcessState.WAITING_SECOND_SUPPLY
                else "assembly_1"
            )
        if self.robot_name == "Dobot_3":
            legacy_start = signals.vision_ok and signals.agv_supply_complete
            return (
                "assembly_1"
                if signals.start or legacy_start
                else None
            )
        legacy_start = signals.product_arrived
        if not (signals.start or legacy_start) or signals.product_type is None:
            return None
        try:
            return {**DOBOT_4_DESTINATIONS, **DOBOT_4_STACK_DESTINATIONS}[
                signals.product_type
            ]
        except KeyError as exc:
            allowed = ", ".join((*DOBOT_4_DESTINATIONS, *DOBOT_4_STACK_DESTINATIONS))
            raise ProcessStateError(f"Dobot_4 제품 종류는 {allowed} 중 하나여야 합니다.") from exc

    def pause(self) -> None:
        """운전 중 현재 이동이 끝난 뒤 다음 이동 단계 진입을 보류한다."""
        with self._lock:
            if self.state is not ProcessState.RUNNING:
                raise ProcessStateError("운전 중일 때만 일시정지할 수 있습니다.")
            self.controller.pause()
            self.state = ProcessState.PAUSED

    def resume(self) -> None:
        """일시정지를 해제하여 남은 공정 단계를 계속 실행한다."""
        with self._lock:
            if self.state is not ProcessState.PAUSED:
                raise ProcessStateError("일시정지 상태가 아닙니다.")
            self.controller.resume()
            self.state = ProcessState.RUNNING

    def emergency_stop(self) -> None:
        """현재 공정을 강제 정지하고 오류 상태로 전환한다."""
        self.controller.emergency_stop()
        with self._lock:
            self.state = ProcessState.ERROR
            self.last_error = "비상정지"

    def reset(self) -> None:
        """오류를 지우되 HOME 완료 여부에 맞는 안전한 대기 상태로 돌아간다."""
        self.controller.reset()
        with self._lock:
            self.last_error = ""
            self.state = (
                ProcessState.WAITING if self._initialized else ProcessState.DISCONNECTED
            )

    def recover_to_home(self, mechanical_home: bool = True) -> list[dict[str, Any]]:
        """공정을 중단·초기화하고 HOME 절차 후 안전한 대기 상태로 복귀한다."""
        with self._lock:
            if self.state is ProcessState.HOMING:
                raise ProcessStateError("이미 HOME 복귀 중입니다.")
            self.state = ProcessState.HOMING
        try:
            # 진행 중일 수 있는 동작을 먼저 강제정지하고 이전 공정을 폐기한다.
            self.controller.emergency_stop()
            self.controller.reset()
            result = self.controller.initialize(mechanical_home=mechanical_home)
        except Exception as exc:
            self._set_error(exc)
            raise
        with self._lock:
            self._initialized = True
            self.last_error = ""
            self.state = ProcessState.WAITING
        return result

    def disconnect(self) -> None:
        """가능한 정지를 요청하고 Worker 연결을 해제한다."""
        try:
            self.controller.emergency_stop()
        finally:
            self.controller.worker.close()
            with self._lock:
                self._initialized = False
                self.state = ProcessState.DISCONNECTED

    def _set_error(self, error: Exception) -> None:
        """발생한 오류를 GUI에서 볼 수 있는 문자열과 오류 상태로 저장한다."""
        with self._lock:
            self.last_error = f"{type(error).__name__}: {error}"
            self.state = ProcessState.ERROR
