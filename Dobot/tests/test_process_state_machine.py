"""공정 상태 머신을 실제 Dobot 없이 검사한다."""

from __future__ import annotations

import unittest

from robot_control.process_state_machine import (
    ProcessSignals,
    ProcessState,
    ProcessStateError,
    RobotProcessStateMachine,
)


class FakeWorker:
    def close(self) -> None:
        pass


class FakeController:
    """호출된 목적지만 기록하며 실제 로봇은 움직이지 않는다."""

    def __init__(self) -> None:
        self.worker = FakeWorker()
        self.destinations: list[str] = []
        self.paused = False
        self.calls: list[object] = []

    def initialize(self, mechanical_home: bool = True) -> list[dict]:
        self.calls.append(("initialize", mechanical_home))
        return [{"home": mechanical_home}]

    def run_cycle(self, destination: str) -> list[dict]:
        self.destinations.append(destination)
        return [{"destination": destination}]

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False
    def emergency_stop(self) -> None: self.calls.append("emergency_stop")
    def reset(self) -> None: self.calls.append("reset")


def ready_machine(robot: str) -> tuple[RobotProcessStateMachine, FakeController]:
    controller = FakeController()
    machine = RobotProcessStateMachine(robot, controller)
    machine.initialize(mechanical_home=False)
    return machine, controller


class ProcessStateMachineTest(unittest.TestCase):
    def test_plc_start_alone_runs_dobot_1_and_3(self) -> None:
        """외부 인터록을 끝낸 PLC의 START 하나로 공정을 시작할 수 있어야 한다."""
        for robot in ("Dobot_1", "Dobot_3"):
            machine, controller = ready_machine(robot)
            machine.process(ProcessSignals(start=True))
            self.assertEqual(controller.destinations, ["assembly_1"])

    def test_dobot_1_requires_supply_and_vision_ok(self) -> None:
        machine, controller = ready_machine("Dobot_1")
        self.assertIsNone(machine.process(ProcessSignals(supply_complete=True)))
        machine.process(ProcessSignals(supply_complete=True, vision_ok=True))
        self.assertEqual(controller.destinations, ["assembly_1"])

    def test_dobot_2_waits_for_second_supply(self) -> None:
        machine, controller = ready_machine("Dobot_2")
        signals = ProcessSignals(supply_complete=True, vision_ok=True, work_count=2)
        machine.process(signals)
        self.assertIs(machine.state, ProcessState.WAITING_SECOND_SUPPLY)
        machine.process(ProcessSignals(supply_complete=True, vision_ok=True))
        self.assertEqual(controller.destinations, ["assembly_1", "assembly_2"])
        self.assertIs(machine.state, ProcessState.WAITING)

    def test_dobot_3_requires_vision_and_agv_complete(self) -> None:
        machine, controller = ready_machine("Dobot_3")
        self.assertIsNone(machine.process(ProcessSignals(vision_ok=True)))
        machine.process(ProcessSignals(vision_ok=True, agv_supply_complete=True))
        self.assertEqual(controller.destinations, ["assembly_1"])

    def test_dobot_4_selects_all_five_return_destinations(self) -> None:
        expected = ["Main", "Lamp_a", "Lamp_b", "Seat_a", "Seat_b"]
        for product_type in expected:
            machine, controller = ready_machine("Dobot_4")
            machine.process(ProcessSignals(product_arrived=True, product_type=product_type))
            expected_destination = (
                "storage_base_1st" if product_type == "Main" else f"return_{product_type}"
            )
            self.assertEqual(controller.destinations, [expected_destination])

    def test_cycle_is_blocked_before_home_initialization(self) -> None:
        machine = RobotProcessStateMachine("Dobot_1", FakeController())
        with self.assertRaises(ProcessStateError):
            machine.process(ProcessSignals(supply_complete=True, vision_ok=True))

    def test_emergency_and_reset_states(self) -> None:
        machine, _controller = ready_machine("Dobot_1")
        machine.emergency_stop()
        self.assertIs(machine.state, ProcessState.ERROR)
        machine.reset()
        self.assertIs(machine.state, ProcessState.WAITING)

    def test_pause_and_resume_change_running_state(self) -> None:
        """pause와 resume은 컨트롤러와 GUI 상태를 함께 변경해야 한다."""
        machine, controller = ready_machine("Dobot_1")
        machine.state = ProcessState.RUNNING

        machine.pause()

        self.assertTrue(controller.paused)
        self.assertIs(machine.state, ProcessState.PAUSED)

        machine.resume()

        self.assertFalse(controller.paused)
        self.assertIs(machine.state, ProcessState.RUNNING)

    def test_recover_to_home_stops_resets_and_reinitializes(self) -> None:
        """원점복귀는 이전 공정을 버리고 정지→초기화→HOME 순서를 지켜야 한다."""
        machine, controller = ready_machine("Dobot_1")
        controller.calls.clear()
        machine.state = ProcessState.ERROR

        result = machine.recover_to_home(mechanical_home=True)

        self.assertEqual(
            controller.calls,
            ["emergency_stop", "reset", ("initialize", True)],
        )
        self.assertEqual(result, [{"home": True}])
        self.assertIs(machine.state, ProcessState.WAITING)


if __name__ == "__main__":
    unittest.main(verbosity=2)
