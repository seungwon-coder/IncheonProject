"""PC 모의 신호의 누락·순서·중복 처리를 실제 Dobot 없이 검사한다."""

from __future__ import annotations

import unittest

from communication.mock_signal_source import MockSignalError, MockSignalSource
from robot_control.process_state_machine import RobotProcessStateMachine


class FakeWorker:
    def close(self) -> None: pass


class FakeController:
    def __init__(self) -> None:
        self.worker = FakeWorker()
        self.destinations: list[str] = []

    def initialize(self, mechanical_home: bool = True) -> list[dict]: return []
    def run_cycle(self, destination: str) -> list[dict]:
        self.destinations.append(destination)
        return [{"destination": destination}]
    def pause(self) -> None: pass
    def resume(self) -> None: pass
    def emergency_stop(self) -> None: pass
    def reset(self) -> None: pass


def ready_machine(robot: str) -> tuple[RobotProcessStateMachine, FakeController]:
    controller = FakeController()
    machine = RobotProcessStateMachine(robot, controller)
    machine.initialize(mechanical_home=False)
    return machine, controller


class MockSignalSourceTest(unittest.TestCase):
    def test_missing_signal_waits_then_different_order_starts_once(self) -> None:
        source = MockSignalSource()
        machine, controller = ready_machine("Dobot_1")
        source.update("Dobot_1", vision_ok=True)
        self.assertIsNone(source.dispatch("Dobot_1", machine))
        source.update("Dobot_1", supply_complete=True)
        source.dispatch("Dobot_1", machine)
        self.assertEqual(controller.destinations, ["assembly_1"])

    def test_held_signal_does_not_duplicate_cycle(self) -> None:
        source = MockSignalSource()
        machine, controller = ready_machine("Dobot_1")
        source.update("Dobot_1", supply_complete=True, vision_ok=True)
        source.dispatch("Dobot_1", machine)
        self.assertFalse(source.update("Dobot_1", supply_complete=True))
        self.assertIsNone(source.dispatch("Dobot_1", machine))
        self.assertEqual(controller.destinations, ["assembly_1"])

    def test_signal_must_turn_off_before_next_cycle(self) -> None:
        source = MockSignalSource()
        machine, controller = ready_machine("Dobot_1")
        source.update("Dobot_1", supply_complete=True, vision_ok=True)
        source.dispatch("Dobot_1", machine)
        source.update("Dobot_1", supply_complete=False)
        source.dispatch("Dobot_1", machine)
        source.update("Dobot_1", supply_complete=True)
        source.dispatch("Dobot_1", machine)
        self.assertEqual(controller.destinations, ["assembly_1", "assembly_1"])

    def test_dobot_2_second_supply_edge_runs_second_position(self) -> None:
        source = MockSignalSource()
        machine, controller = ready_machine("Dobot_2")
        source.update(
            "Dobot_2", supply_complete=True, vision_ok=True, work_count=2
        )
        source.dispatch("Dobot_2", machine)
        source.update("Dobot_2", supply_complete=False)
        source.dispatch("Dobot_2", machine)
        source.update("Dobot_2", supply_complete=True)
        source.dispatch("Dobot_2", machine)
        self.assertEqual(controller.destinations, ["assembly_1", "assembly_2"])

    def test_dobot_3_accepts_agv_then_vision_order(self) -> None:
        source = MockSignalSource()
        machine, controller = ready_machine("Dobot_3")
        source.update("Dobot_3", agv_supply_complete=True)
        self.assertIsNone(source.dispatch("Dobot_3", machine))
        source.update("Dobot_3", vision_ok=True)
        source.dispatch("Dobot_3", machine)
        self.assertEqual(controller.destinations, ["assembly_1"])

    def test_dobot_4_product_type_and_arrival_select_destination(self) -> None:
        source = MockSignalSource()
        machine, controller = ready_machine("Dobot_4")
        source.update("Dobot_4", product_type="Seat_b")
        self.assertIsNone(source.dispatch("Dobot_4", machine))
        source.update("Dobot_4", product_arrived=True)
        source.dispatch("Dobot_4", machine)
        self.assertEqual(controller.destinations, ["return_Seat_b"])

    def test_invalid_work_count_and_unknown_signal_are_rejected(self) -> None:
        source = MockSignalSource()
        with self.assertRaises(MockSignalError):
            source.update("Dobot_2", work_count=3)
        with self.assertRaises(MockSignalError):
            source.update("Dobot_1", typo_signal=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
