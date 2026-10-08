"""통합 GUI의 실제 Dobot 공정 운전을 관리한다.

상태 조회에 이미 연결된 DobotFleet의 Worker를 그대로 사용한다. 같은 COM 포트를
새 Worker가 다시 열지 않으므로 통신 충돌을 예방할 수 있다.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
from typing import Any

from robot_control.dobot_config import ROBOT_NAMES
from robot_control.dobot_fleet import DobotFleet
from robot_control.process_state_machine import ProcessSignals, RobotProcessStateMachine
from communication.plc_handshake import RobotPlcHandshake, RobotResponseSignals
from robot_control.robot_motion_controller import (
    AUTOMATIC_CYCLE_MAX_PTP_SPEED_PERCENT,
    MANUAL_CYCLE_MAX_PTP_SPEED_PERCENT,
    RobotMotionController,
)
from robot_control.main_warehouse_inventory import MainWarehouseInventory


class LiveProcessManagerError(RuntimeError):
    """실제 Worker가 준비되지 않았거나 운전 조건이 맞지 않을 때 발생한다."""


class LiveProcessManager:
    """연결된 네 Worker와 실제 공정 상태 머신을 결합한다."""

    def __init__(
        self,
        fleet: DobotFleet,
        controller_factory: Callable[[str, Any], Any] = RobotMotionController,
        main_inventory: MainWarehouseInventory | None = None,
    ) -> None:
        self.fleet = fleet
        self.main_inventory = main_inventory or MainWarehouseInventory()
        self.controllers = {
            name: controller_factory(name, fleet.workers[name]) for name in ROBOT_NAMES
        }
        self.machines = {
            name: RobotProcessStateMachine(name, self.controllers[name])
            for name in ROBOT_NAMES
        }
        self.handshakes = {name: RobotPlcHandshake(name) for name in ROBOT_NAMES}
        self._plc_error_before_disconnect = {name: False for name in ROBOT_NAMES}
        self._ready_approved = {name: False for name in ROBOT_NAMES}
        self._base_supply_lock = threading.RLock()
        self._base_supply_finish = False
        self._base_supply_finish_published_at: float | None = None
        # Dobot_4 차량 하부 입고·출고가 참조하는 재고와 작업자 수동 변경이
        # 동시에 진행되지 않도록 사이클 전체를 보호한다.
        self._main_inventory_cycle_lock = threading.Lock()

    def initialize(self, robot_name: str) -> list[dict[str, Any]]:
        """기계적 HOME 후 사용자 READY 위치까지 실제로 이동한다."""
        handshake = self._handshake(robot_name)
        self._ready_approved[robot_name] = False
        handshake.mark_running()
        try:
            result = self._machine(robot_name).initialize(mechanical_home=True)
        except Exception:
            handshake.mark_error()
            raise
        # 이동 완료 후 PLC READY는 작업자 버튼 승인까지 OFF로 유지한다.
        handshake.mark_disconnected()
        self._plc_error_before_disconnect[robot_name] = False
        return result

    def approve_ready(self, robot_name: str) -> None:
        """작업자 판단으로 HOME·좌표 검사 없이 선택 로봇 READY를 켠다."""
        if self._plc_error_before_disconnect[robot_name] or self.plc_response(robot_name).error:
            raise LiveProcessManagerError("기존 오류를 초기화한 뒤 READY를 승인하세요.")
        response = self.plc_response(robot_name)
        if response.running or response.finish:
            raise LiveProcessManagerError("공정 및 FINISH 신호가 끝난 뒤 READY를 승인하세요.")
        self._machine(robot_name).accept_operator_ready()
        self._ready_approved[robot_name] = True
        self._handshake(robot_name).mark_ready()

    def process(
        self, robot_name: str, signals: ProcessSignals, *, automatic: bool = False
    ) -> list[dict[str, Any]] | None:
        """현재 입력이 인터록을 만족하면 선택 로봇의 실제 1사이클을 실행한다."""
        speed_limit = (
            AUTOMATIC_CYCLE_MAX_PTP_SPEED_PERCENT
            if automatic
            else MANUAL_CYCLE_MAX_PTP_SPEED_PERCENT
        )
        controller = self.controllers[robot_name]
        if hasattr(controller, "set_cycle_speed_limit"):
            controller.set_cycle_speed_limit(speed_limit)
        else:
            # 장비 없는 자동 테스트용 가짜 컨트롤러에도 선택 모드를 기록한다.
            controller.cycle_max_ptp_speed_percent = speed_limit
        handshake = self._handshake(robot_name)
        # 자동 운전은 공정 스레드 시작 직전에 RUNNING을 먼저 PLC에 내보낸다.
        # 그 짧은 구간만 이전 READY 승인 상태로 기동을 이어받는다.
        pre_admitted = (
            handshake.response.running
            and self._ready_approved[robot_name]
            and self.state(robot_name) in ("waiting", "waiting_second_supply")
        )
        if not handshake.response.ready and not pre_admitted:
            raise LiveProcessManagerError("PLC READY가 꺼져 있습니다. READY 승인 버튼을 누르세요.")
        main_deposit = robot_name == "Dobot_4" and signals.product_type == "Main"
        inventory_lock_acquired = False
        try:
            if robot_name == "Dobot_4":
                self._main_inventory_cycle_lock.acquire()
                inventory_lock_acquired = True
            if main_deposit:
                # 이동 전에 다음 빈 단을 고정한다. 창고가 가득 차면 여기서 차단되어
                # 실제 로봇에는 어떤 이동 명령도 전달되지 않는다.
                point = self.main_inventory.next_deposit_point()
                level = point.removeprefix("storage_base_")
                signals = ProcessSignals(
                    **{**signals.__dict__, "product_type": f"Main_{level}"}
                )
            # 수동 GUI 기동도 실제 이동이 승인된 시점부터 PLC RUNNING에 반영한다.
            # 인터록 미충족으로 이동하지 않는 경우에는 RUNNING을 켜지 않는다.
            result = self._machine(robot_name).process(
                signals, on_start=handshake.mark_running
            )
            if result is not None:
                # ready 복귀까지 포함한 전체 사이클이 정상 종료된 뒤에만 +1 한다.
                if main_deposit:
                    self.main_inventory.confirm_deposit()
                handshake.mark_finished()
            return result
        except Exception:
            handshake.mark_error()
            raise
        finally:
            if inventory_lock_acquired:
                self._main_inventory_cycle_lock.release()

    def process_plc_start(
        self,
        robot_name: str,
        start: bool,
        *,
        work_count: int = 1,
        product_type: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """PLC START 상승 에지 한 번만 소비해 실제 공정을 실행한다."""
        handshake = self._handshake(robot_name)
        if not handshake.set_start(start):
            return None
        return self.process(
            robot_name,
            ProcessSignals(start=True, work_count=work_count, product_type=product_type),
        )

    def process_dobot_4_base_supply(
        self, *, automatic: bool = False
    ) -> list[dict[str, Any]]:
        """현재 창고 최상단 차량 하부를 메인 컨베이어로 공급한다."""
        robot_name = "Dobot_4"
        speed_limit = (
            AUTOMATIC_CYCLE_MAX_PTP_SPEED_PERCENT
            if automatic
            else MANUAL_CYCLE_MAX_PTP_SPEED_PERCENT
        )
        controller = self.controllers[robot_name]
        if hasattr(controller, "set_cycle_speed_limit"):
            controller.set_cycle_speed_limit(speed_limit)
        else:
            controller.cycle_max_ptp_speed_percent = speed_limit
        handshake = self._handshake(robot_name)
        pre_admitted = (
            handshake.response.running
            and self._ready_approved[robot_name]
            and self.state(robot_name) == "waiting"
        )
        if not handshake.response.ready and not pre_admitted:
            raise LiveProcessManagerError("PLC READY가 꺼져 있습니다. READY 승인 버튼을 누르세요.")
        if self.dobot_4_base_supply_finish():
            raise LiveProcessManagerError("이전 베이스 공급 FINISH 펄스가 끝나지 않았습니다.")
        self._main_inventory_cycle_lock.acquire()
        try:
            # 이동을 시작하기 전에 현재 최상단을 고정한다. 재고가 0이면 로봇은
            # 움직이지 않고 오류로 전환한다.
            storage_point = self.main_inventory.current_pickup_point()
            result = self._machine(robot_name).process_dobot_4_base_supply(
                storage_point, on_start=handshake.mark_running
            )
            # READY 복귀까지 정상 완료된 경우에만 재고를 한 개 줄인다.
            self.main_inventory.confirm_supply()
            handshake.mark_ready()
            with self._base_supply_lock:
                self._base_supply_finish = True
                self._base_supply_finish_published_at = None
            return result
        except Exception:
            with self._base_supply_lock:
                self._base_supply_finish = False
                self._base_supply_finish_published_at = None
            handshake.mark_error()
            raise
        finally:
            self._main_inventory_cycle_lock.release()

    def set_main_inventory_count(self, count: int) -> int:
        """Dobot_4 대기 중에만 작업자가 확인한 실제 창고 수량을 반영한다."""
        if self.state("Dobot_4") != "waiting" or self.plc_response("Dobot_4").running:
            raise LiveProcessManagerError(
                "Dobot_4 운전 중에는 차량 하부 재고를 변경할 수 없습니다."
            )
        if not self._main_inventory_cycle_lock.acquire(blocking=False):
            raise LiveProcessManagerError(
                "Dobot_4 입고·출고 처리 중에는 차량 하부 재고를 변경할 수 없습니다."
            )
        try:
            return self.main_inventory.set_count(count)
        finally:
            self._main_inventory_cycle_lock.release()

    def dobot_4_base_supply_finish(self) -> bool:
        """FINISH(B)(4)를 첫 OPC 기록 후 약 1초 동안 ON으로 유지한다."""
        with self._base_supply_lock:
            if (
                self._base_supply_finish
                and self._base_supply_finish_published_at is not None
                and time.monotonic() - self._base_supply_finish_published_at
                >= RobotPlcHandshake.FINISH_PULSE_SECONDS
            ):
                self._base_supply_finish = False
                self._base_supply_finish_published_at = None
            return self._base_supply_finish

    def mark_dobot_4_base_supply_finish_published(self) -> None:
        """FINISH(B)(4)의 최초 성공 기록 시점부터 펄스 시간을 계산한다."""
        with self._base_supply_lock:
            if self._base_supply_finish and self._base_supply_finish_published_at is None:
                self._base_supply_finish_published_at = time.monotonic()

    def accept_current_ready(self, robot_name: str, tolerance: float = 0.5) -> dict[str, Any]:
        """Worker의 현재 좌표가 사용자 READY일 때만 대기 완료를 인계한다."""
        status = self.fleet.workers[robot_name].get_status()
        self._machine(robot_name).accept_verified_ready(status["pose"], tolerance)
        self._handshake(robot_name).mark_ready()
        self._ready_approved[robot_name] = True
        self._plc_error_before_disconnect[robot_name] = False
        return status

    def accept_current_waiting_pose(
        self, robot_name: str, tolerance: float = 0.5
    ) -> dict[str, Any]:
        """사용자 READY와 현재 좌표가 맞을 때 상태를 인계한다."""
        status = self.fleet.workers[robot_name].get_status()
        self._machine(robot_name).accept_verified_waiting_pose(
            "ready", status["pose"], tolerance
        )
        self._handshake(robot_name).mark_ready()
        self._ready_approved[robot_name] = True
        self._plc_error_before_disconnect[robot_name] = False
        return status

    def emergency_stop(self, robot_name: str) -> None:
        self._machine(robot_name).emergency_stop()
        # 통신은 유지되므로 PLC에도 비상정지 상태를 ERROR로 즉시 알린다.
        self._handshake(robot_name).mark_error()
        self._ready_approved[robot_name] = False

    def pause(self, robot_name: str) -> None:
        """선택 로봇이 현재 이동 구간을 마친 뒤 다음 공정 단계에서 대기한다."""
        self._machine(robot_name).pause()

    def resume(self, robot_name: str) -> None:
        """일시정지된 선택 로봇이 남은 공정 단계를 계속 실행한다."""
        self._machine(robot_name).resume()

    def reset(self, robot_name: str) -> None:
        self._machine(robot_name).reset()
        # 이동 없는 초기화 뒤에는 작업자가 READY를 다시 승인해야 한다.
        self._handshake(robot_name).mark_disconnected()
        self._ready_approved[robot_name] = False
        self._plc_error_before_disconnect[robot_name] = False

    def recover_to_home(
        self, robot_name: str, mechanical_home: bool = True
    ) -> list[dict[str, Any]]:
        """선택 로봇을 정지·초기화하고 사용자 READY 위치로 실제 복귀시킨다."""
        handshake = self._handshake(robot_name)
        self._ready_approved[robot_name] = False
        handshake.mark_running()
        try:
            result = self._machine(robot_name).recover_to_home(mechanical_home)
        except Exception:
            handshake.mark_error()
            raise
        handshake.mark_disconnected()
        self._plc_error_before_disconnect[robot_name] = False
        return result

    def reset_all(self) -> None:
        """네 로봇의 공정 상태를 이동 없이 각각 초기화한다."""
        for robot_name in ROBOT_NAMES:
            self.reset(robot_name)

    def recover_all_to_home(
        self, mechanical_home: bool = True
    ) -> dict[str, list[dict[str, Any]]]:
        """독립 Worker 네 개를 사용해 네 로봇의 HOME 복귀를 동시에 시작한다.

        한 로봇이 실패해도 다른 로봇의 복귀가 끝날 때까지 결과를 수집한다. 작업 공간
        간섭 여부는 실행 전 사용자가 반드시 확인해야 한다.
        """
        results: dict[str, list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(ROBOT_NAMES)) as executor:
            futures = {
                executor.submit(self.recover_to_home, name, mechanical_home): name
                for name in ROBOT_NAMES
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as exc:
                    errors[name] = f"{type(exc).__name__}: {exc}"
        if errors:
            detail = " / ".join(f"{name}={error}" for name, error in errors.items())
            raise LiveProcessManagerError(f"4대 동시 원점복귀 일부 실패: {detail}")
        # 완료 순서와 관계없이 항상 Dobot_1~4 순서로 결과를 돌려준다.
        return {name: results[name] for name in ROBOT_NAMES}

    def state(self, robot_name: str) -> str:
        return self._machine(robot_name).state.value

    def plc_response(self, robot_name: str) -> RobotResponseSignals:
        """OPC 출력 계층이 읽을 현재 RUNNING·FINISH·ERROR·READY 상태."""
        return self._handshake(robot_name).response

    def mark_finish_published(self, robot_name: str) -> None:
        """OPC-UA가 FINISH ON을 쓴 뒤 완료 펄스 타이머를 시작한다."""
        self._handshake(robot_name).mark_finish_published()

    def plc_output_by_address(self, robot_name: str) -> dict[str, bool]:
        """현재 응답을 확정된 Mitsubishi M 주소 기준으로 반환한다."""
        return self._handshake(robot_name).output_by_address()

    def mark_plc_disconnected(self, _error: str = "") -> None:
        """OPC 단절 시 네 로봇의 PLC 응답 상태를 모두 안전한 OFF로 바꾼다.

        재연결 때 기존 작업자 승인이 있던 대기 로봇만 READY로 복구한다.
        기존 PLC 오류는 작업 초기화 전까지 보존한다.
        """
        for name, handshake in self.handshakes.items():
            self._plc_error_before_disconnect[name] |= handshake.response.error
            handshake.mark_disconnected()
        with self._base_supply_lock:
            self._base_supply_finish = False
            self._base_supply_finish_published_at = None

    def restore_plc_ready_after_reconnect(self) -> dict[str, str]:
        """OPC 재연결 후 기존 작업자 READY 승인을 복구한다."""
        results: dict[str, str] = {}
        for name in ROBOT_NAMES:
            if self.state(name) not in ("waiting", "waiting_second_supply"):
                continue
            if self._plc_error_before_disconnect[name]:
                results[name] = "기존 PLC 오류: 작업 초기화 후 READY 승인 필요"
                continue
            if not self._ready_approved[name]:
                results[name] = "작업자 READY 승인 필요"
                continue
            self.handshakes[name].mark_ready()
            results[name] = "작업자 READY 승인 복구 완료"
        return results

    def _machine(self, robot_name: str) -> RobotProcessStateMachine:
        try:
            return self.machines[robot_name]
        except KeyError as exc:
            raise LiveProcessManagerError(f"등록되지 않은 로봇: {robot_name}") from exc

    def _handshake(self, robot_name: str) -> RobotPlcHandshake:
        try:
            return self.handshakes[robot_name]
        except KeyError as exc:
            raise LiveProcessManagerError(f"등록되지 않은 로봇: {robot_name}") from exc
