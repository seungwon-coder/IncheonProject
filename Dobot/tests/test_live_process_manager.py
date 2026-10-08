"""실제 장비 대신 가짜 컨트롤러로 실제 공정 관리자 연결을 검사한다."""

import threading
import time
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock

from robot_control.dobot_config import ROBOT_NAMES
from robot_control.live_process_manager import LiveProcessManager, LiveProcessManagerError
from robot_control.process_state_machine import ProcessSignals
from robot_control.main_warehouse_inventory import (
    MainWarehouseInventory,
    MainWarehouseInventoryError,
)


class FakeWorker:
    def get_status(self):
        return {"pose": {axis: 0.0 for axis in "xyzr"}, "alarms": []}


class FakeFleet:
    def __init__(self) -> None:
        self.workers = {name: FakeWorker() for name in ROBOT_NAMES}


class FakeController:
    def __init__(self, robot_name, worker) -> None:
        self.robot_name = robot_name
        self.worker = worker
        self.destinations = []
        self.waiting_point = None

    def initialize(self, mechanical_home=True):
        self.waiting_point = "ready"
        return [{"home": mechanical_home}]

    def confirm_ready_pose(self, pose, tolerance=0.5):
        if self.waiting_point != "ready":
            raise RuntimeError("READY 위치 미확인")

    def accept_operator_ready(self):
        self.waiting_point = "ready"

    def run_cycle(self, destination):
        self.destinations.append(destination)
        return [{"destination": destination}]

    def run_base_supply_cycle(self, storage_point):
        self.destinations.append((storage_point, "main_conveyor"))
        return [{"pickup": storage_point, "destination": "main_conveyor"}]

    def emergency_stop(self):
        pass

    def pause(self):
        pass

    def resume(self):
        pass

    def reset(self):
        self.waiting_point = None


class LiveProcessManagerTests(unittest.TestCase):
    def initialize_and_approve(self, manager, robot_name):
        manager.initialize(robot_name)
        self.assertFalse(manager.plc_response(robot_name).ready)
        manager.approve_ready(robot_name)

    def test_home_requires_operator_ready_approval_before_cycle(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        manager.initialize("Dobot_1")

        self.assertEqual(manager.state("Dobot_1"), "waiting")
        self.assertFalse(manager.plc_response("Dobot_1").ready)
        self.assertIsNone(manager.process_plc_start("Dobot_1", True))
        with self.assertRaises(LiveProcessManagerError):
            manager.process("Dobot_1", ProcessSignals(start=True))
        self.assertEqual(manager.controllers["Dobot_1"].destinations, [])

        manager.approve_ready("Dobot_1")
        self.assertTrue(manager.plc_response("Dobot_1").ready)

    def test_automatic_cycle_uses_30_percent_cap_and_manual_uses_10(self) -> None:
        manual = LiveProcessManager(FakeFleet(), FakeController)
        self.initialize_and_approve(manual, "Dobot_1")
        manual.process("Dobot_1", ProcessSignals(start=True))
        self.assertEqual(
            manual.controllers["Dobot_1"].cycle_max_ptp_speed_percent, 10.0
        )

        automatic = LiveProcessManager(FakeFleet(), FakeController)
        self.initialize_and_approve(automatic, "Dobot_1")
        automatic.process(
            "Dobot_1", ProcessSignals(start=True), automatic=True
        )
        self.assertEqual(
            automatic.controllers["Dobot_1"].cycle_max_ptp_speed_percent, 30.0
        )

    def test_ready_button_bypasses_home_pose_and_alarm_checks(self) -> None:
        fleet = FakeFleet()
        manager = LiveProcessManager(fleet, FakeController)
        worker = fleet.workers["Dobot_1"]
        worker.get_status = Mock(side_effect=AssertionError("상태를 조회하지 않아야 함"))
        manager.controllers["Dobot_1"].confirm_ready_pose = Mock(
            side_effect=AssertionError("좌표를 검사하지 않아야 함")
        )

        manager.approve_ready("Dobot_1")

        self.assertEqual(manager.state("Dobot_1"), "waiting")
        self.assertEqual(manager.controllers["Dobot_1"].waiting_point, "ready")
        self.assertTrue(manager.plc_response("Dobot_1").ready)
        worker.get_status.assert_not_called()
        manager.controllers["Dobot_1"].confirm_ready_pose.assert_not_called()

    def test_operator_ready_without_home_can_start_cycle(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        manager.approve_ready("Dobot_2")

        result = manager.process("Dobot_2", ProcessSignals(start=True))

        self.assertEqual(result, [{"destination": "assembly_1"}])
        self.assertEqual(manager.controllers["Dobot_2"].destinations, ["assembly_1"])

    def test_operator_ready_rejected_while_running_or_in_error(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        manager.approve_ready("Dobot_1")
        manager.handshakes["Dobot_1"].mark_running()
        with self.assertRaises(LiveProcessManagerError):
            manager.approve_ready("Dobot_1")
        manager.handshakes["Dobot_1"].mark_error()
        with self.assertRaises(LiveProcessManagerError):
            manager.approve_ready("Dobot_1")

    def test_reconnect_does_not_bypass_pending_ready_approval(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        manager.initialize("Dobot_1")
        manager.mark_plc_disconnected()

        result = manager.restore_plc_ready_after_reconnect()

        self.assertEqual(result["Dobot_1"], "작업자 READY 승인 필요")
        self.assertFalse(manager.plc_response("Dobot_1").ready)

    def make_inventory(self, count=0):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        inventory = MainWarehouseInventory(Path(temporary.name) / "inventory.json")
        inventory.set_count(count)
        return inventory

    def test_dobot_4_main_return_selects_next_level_and_increments(self) -> None:
        inventory = self.make_inventory(1)
        manager = LiveProcessManager(FakeFleet(), FakeController, inventory)
        self.initialize_and_approve(manager, "Dobot_4")

        manager.process("Dobot_4", ProcessSignals(start=True, product_type="Main"))

        self.assertEqual(manager.controllers["Dobot_4"].destinations, ["storage_base_2nd"])
        self.assertEqual(inventory.count, 2)

    def test_dobot_4_full_main_warehouse_blocks_before_motion(self) -> None:
        inventory = self.make_inventory(3)
        manager = LiveProcessManager(FakeFleet(), FakeController, inventory)
        self.initialize_and_approve(manager, "Dobot_4")

        with self.assertRaises(MainWarehouseInventoryError):
            manager.process("Dobot_4", ProcessSignals(start=True, product_type="Main"))

        self.assertEqual(manager.controllers["Dobot_4"].destinations, [])
        self.assertEqual(inventory.count, 3)

    def test_failed_main_return_does_not_increment_inventory(self) -> None:
        class FailingController(FakeController):
            def run_cycle(self, destination):
                raise RuntimeError("가상 이동 실패")

        inventory = self.make_inventory(1)
        manager = LiveProcessManager(FakeFleet(), FailingController, inventory)
        self.initialize_and_approve(manager, "Dobot_4")

        with self.assertRaises(RuntimeError):
            manager.process("Dobot_4", ProcessSignals(start=True, product_type="Main"))
        self.assertEqual(inventory.count, 1)

    def test_dobot_4_base_supply_uses_top_level_and_decrements_inventory(self) -> None:
        inventory = self.make_inventory(3)
        manager = LiveProcessManager(FakeFleet(), FakeController, inventory)
        self.initialize_and_approve(manager, "Dobot_4")

        result = manager.process_dobot_4_base_supply()

        self.assertEqual(result, [{
            "pickup": "storage_base_3rd", "destination": "main_conveyor"
        }])
        self.assertEqual(inventory.count, 2)
        self.assertTrue(manager.dobot_4_base_supply_finish())
        self.assertFalse(manager.plc_response("Dobot_4").finish)
        self.assertTrue(manager.plc_response("Dobot_4").ready)

    def test_empty_dobot_4_base_supply_blocks_before_motion(self) -> None:
        inventory = self.make_inventory(0)
        manager = LiveProcessManager(FakeFleet(), FakeController, inventory)
        self.initialize_and_approve(manager, "Dobot_4")

        with self.assertRaises(MainWarehouseInventoryError):
            manager.process_dobot_4_base_supply()

        self.assertEqual(manager.controllers["Dobot_4"].destinations, [])
        self.assertEqual(inventory.count, 0)
        self.assertTrue(manager.plc_response("Dobot_4").error)

    def test_failed_dobot_4_base_supply_does_not_decrement_inventory(self) -> None:
        class FailingController(FakeController):
            def run_base_supply_cycle(self, _storage_point):
                raise RuntimeError("가상 공급 실패")

        inventory = self.make_inventory(2)
        manager = LiveProcessManager(FakeFleet(), FailingController, inventory)
        self.initialize_and_approve(manager, "Dobot_4")

        with self.assertRaises(RuntimeError):
            manager.process_dobot_4_base_supply()

        self.assertEqual(inventory.count, 2)
        self.assertFalse(manager.dobot_4_base_supply_finish())
        self.assertTrue(manager.plc_response("Dobot_4").error)

    def test_dobot_4_base_finish_pulses_after_first_successful_publish(self) -> None:
        inventory = self.make_inventory(1)
        manager = LiveProcessManager(FakeFleet(), FakeController, inventory)
        self.initialize_and_approve(manager, "Dobot_4")
        manager.process_dobot_4_base_supply()

        manager.mark_dobot_4_base_supply_finish_published()
        manager._base_supply_finish_published_at = time.monotonic() - 1.1

        self.assertFalse(manager.dobot_4_base_supply_finish())

    def test_operator_can_change_main_inventory_while_dobot_4_is_waiting(self) -> None:
        inventory = self.make_inventory(1)
        manager = LiveProcessManager(FakeFleet(), FakeController, inventory)
        self.initialize_and_approve(manager, "Dobot_4")

        result = manager.set_main_inventory_count(3)

        self.assertEqual(result, 3)
        self.assertEqual(inventory.count, 3)

    def test_operator_cannot_change_main_inventory_while_dobot_4_is_running(self) -> None:
        inventory = self.make_inventory(1)
        manager = LiveProcessManager(FakeFleet(), FakeController, inventory)
        self.initialize_and_approve(manager, "Dobot_4")
        manager.handshakes["Dobot_4"].mark_running()

        with self.assertRaises(LiveProcessManagerError):
            manager.set_main_inventory_count(3)

        self.assertEqual(inventory.count, 1)

    def test_plc_start_drives_response_handshake(self) -> None:
        """START OFF가 완료 펄스를 조기에 지우지 않아야 한다."""
        manager = LiveProcessManager(FakeFleet(), FakeController)
        manager.machines["Dobot_1"].initialize(mechanical_home=False)
        manager.handshakes["Dobot_1"].mark_ready()

        result = manager.process_plc_start("Dobot_1", True)

        self.assertIsNotNone(result)
        self.assertTrue(manager.plc_response("Dobot_1").finish)
        self.assertIsNone(manager.process_plc_start("Dobot_1", True))
        manager.process_plc_start("Dobot_1", False)
        self.assertTrue(manager.plc_response("Dobot_1").finish)

    def test_plc_disconnect_clears_all_output_states(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        for handshake in manager.handshakes.values():
            handshake.mark_ready()
        manager.mark_plc_disconnected("시험 단절")
        for robot in ROBOT_NAMES:
            self.assertEqual(
                set(manager.plc_output_by_address(robot).values()), {False}
            )

    def test_opc_reconnect_restores_previous_operator_approval(self) -> None:
        fleet = FakeFleet()
        manager = LiveProcessManager(fleet, FakeController)
        self.initialize_and_approve(manager, "Dobot_1")
        controller = manager.controllers["Dobot_1"]
        controller.waiting_point = "ready"
        controller.confirm_ready_pose = Mock()
        fleet.workers["Dobot_1"].get_status = Mock(return_value={
            "pose": {"x": 1.0, "y": 2.0, "z": 3.0, "r": 4.0}, "alarms": []
        })
        manager.mark_plc_disconnected()

        result = manager.restore_plc_ready_after_reconnect()

        self.assertIn("Dobot_1", result)
        self.assertTrue(manager.plc_response("Dobot_1").ready)
        controller.confirm_ready_pose.assert_not_called()
        self.assertFalse(manager.plc_response("Dobot_2").ready)

    def test_opc_reconnect_does_not_recheck_pose_after_operator_approval(self) -> None:
        fleet = FakeFleet()
        manager = LiveProcessManager(fleet, FakeController)
        self.initialize_and_approve(manager, "Dobot_1")
        controller = manager.controllers["Dobot_1"]
        controller.waiting_point = "ready"
        controller.confirm_ready_pose = Mock(side_effect=RuntimeError("좌표 불일치"))
        fleet.workers["Dobot_1"].get_status = Mock(return_value={
            "pose": {"x": 0.0, "y": 0.0, "z": 0.0, "r": 0.0}, "alarms": []
        })
        manager.mark_plc_disconnected()

        result = manager.restore_plc_ready_after_reconnect()

        self.assertEqual(result["Dobot_1"], "작업자 READY 승인 복구 완료")
        self.assertTrue(manager.plc_response("Dobot_1").ready)
        controller.confirm_ready_pose.assert_not_called()

    def test_opc_reconnect_does_not_clear_previous_plc_error(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        self.initialize_and_approve(manager, "Dobot_1")
        manager.controllers["Dobot_1"].waiting_point = "ready"
        manager.handshakes["Dobot_1"].mark_error()
        manager.mark_plc_disconnected()

        result = manager.restore_plc_ready_after_reconnect()

        self.assertIn("기존 PLC 오류", result["Dobot_1"])
        self.assertFalse(manager.plc_response("Dobot_1").ready)

    def test_manual_reset_requires_new_operator_approval(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        self.initialize_and_approve(manager, "Dobot_1")
        self.assertTrue(manager.plc_response("Dobot_1").ready)

        manager.reset("Dobot_1")

        self.assertFalse(manager.plc_response("Dobot_1").ready)
        manager.approve_ready("Dobot_1")
        self.assertTrue(manager.plc_response("Dobot_1").ready)

    def test_emergency_stop_publishes_error(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        self.initialize_and_approve(manager, "Dobot_1")

        manager.emergency_stop("Dobot_1")

        self.assertTrue(manager.plc_response("Dobot_1").error)
        self.assertFalse(manager.plc_response("Dobot_1").ready)

    def test_home_then_interlocked_cycle_uses_existing_worker(self) -> None:
        fleet = FakeFleet()
        manager = LiveProcessManager(fleet, FakeController)
        self.initialize_and_approve(manager, "Dobot_1")
        result = manager.process(
            "Dobot_1", ProcessSignals(supply_complete=True, vision_ok=True)
        )
        self.assertEqual(result, [{"destination": "assembly_1"}])
        self.assertIs(manager.controllers["Dobot_1"].worker, fleet.workers["Dobot_1"])

    def test_cycle_is_blocked_before_home(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        with self.assertRaises(Exception):
            manager.process(
                "Dobot_3",
                ProcessSignals(vision_ok=True, agv_supply_complete=True),
            )

    def test_pause_and_resume_are_forwarded_to_running_robot(self) -> None:
        """일시정지와 재개가 선택 로봇 상태 머신에 차례로 전달되어야 한다."""
        started = threading.Event()
        release = threading.Event()

        class BlockingController(FakeController):
            def run_cycle(self, destination):
                started.set()
                release.wait(timeout=1.0)
                return super().run_cycle(destination)

        manager = LiveProcessManager(FakeFleet(), BlockingController)
        self.initialize_and_approve(manager, "Dobot_1")
        process_thread = threading.Thread(
            target=lambda: manager.process(
                "Dobot_1", ProcessSignals(supply_complete=True, vision_ok=True)
            )
        )
        process_thread.start()
        self.assertTrue(started.wait(timeout=1.0))

        manager.pause("Dobot_1")
        self.assertEqual(manager.state("Dobot_1"), "paused")

        manager.resume("Dobot_1")
        self.assertEqual(manager.state("Dobot_1"), "running")

        release.set()
        # 가짜 컨트롤러는 실제 pause 대기를 하지 않으므로 시험 스레드만 정리한다.
        process_thread.join(timeout=1.0)

    def test_manual_cycle_publishes_running_only_during_actual_motion(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class BlockingController(FakeController):
            def run_cycle(self, destination):
                started.set()
                release.wait(timeout=2.0)
                return super().run_cycle(destination)

        manager = LiveProcessManager(FakeFleet(), BlockingController)
        self.initialize_and_approve(manager, "Dobot_1")
        thread = threading.Thread(
            target=lambda: manager.process("Dobot_1", ProcessSignals(start=True))
        )
        try:
            thread.start()
            self.assertTrue(started.wait(timeout=1.0))
            self.assertTrue(manager.plc_response("Dobot_1").running)
            self.assertFalse(manager.plc_response("Dobot_1").ready)
        finally:
            release.set()
            thread.join(timeout=2.0)
        self.assertFalse(manager.plc_response("Dobot_1").running)
        self.assertTrue(manager.plc_response("Dobot_1").finish)

    def test_manual_cycle_without_start_keeps_ready(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        self.initialize_and_approve(manager, "Dobot_1")

        self.assertIsNone(manager.process("Dobot_1", ProcessSignals()))

        self.assertTrue(manager.plc_response("Dobot_1").ready)
        self.assertFalse(manager.plc_response("Dobot_1").running)

    def test_recover_to_home_is_applied_to_selected_robot_only(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        result = manager.recover_to_home("Dobot_2", mechanical_home=True)

        self.assertEqual(result, [{"home": True}])
        self.assertEqual(manager.state("Dobot_2"), "waiting")
        self.assertEqual(manager.state("Dobot_1"), "disconnected")
        self.assertFalse(manager.plc_response("Dobot_2").ready)

    def test_recover_all_to_home_makes_every_robot_wait(self) -> None:
        manager = LiveProcessManager(FakeFleet(), FakeController)
        results = manager.recover_all_to_home(mechanical_home=True)

        self.assertEqual(tuple(results), ROBOT_NAMES)
        self.assertTrue(all(manager.state(name) == "waiting" for name in ROBOT_NAMES))

    def test_recover_all_to_home_runs_robots_in_parallel(self) -> None:
        """4대 HOME 시작이 순차 실행으로 되돌아가지 않도록 병렬성을 확인한다."""
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        class SlowController(FakeController):
            def initialize(self, mechanical_home=True):
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                time.sleep(0.03)
                with lock:
                    active -= 1
                return super().initialize(mechanical_home)

        manager = LiveProcessManager(FakeFleet(), SlowController)
        manager.recover_all_to_home(mechanical_home=True)

        self.assertGreater(maximum_active, 1)


if __name__ == "__main__":
    unittest.main()
