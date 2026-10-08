"""공통 안전 이동 순서를 실제 Dobot 없이 검사한다."""

from __future__ import annotations

import unittest

from robot_control.robot_motion_controller import (
    AUTOMATIC_CYCLE_MAX_PTP_SPEED_PERCENT,
    GRIPPER_SETTLE_SECONDS,
    MANUAL_CYCLE_MAX_PTP_SPEED_PERCENT,
    SUCTION_SETTLE_SECONDS,
    MotionControllerError,
    RobotMotionController,
)
from teaching.teaching_points import TeachingPointError


class FakeWorker:
    """명령을 기록만 하고 실제 장비에는 아무것도 보내지 않는 가짜 Worker."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def move_ptp(self, target: dict[str, float], **options: object) -> dict[str, object]:
        self.calls.append(("move", target))
        return {"after": target, "options": options}

    def set_end_effector(self, tool: str, action: str) -> dict[str, str]:
        self.calls.append((tool, action))
        return {"tool": tool, "action": action}

    def home(self) -> dict[str, bool]:
        self.calls.append(("home", True))
        return {"homed": True}

    def stop_jog(self) -> None:
        self.calls.append(("stop_jog", True))

    def stop_queue(self) -> None:
        self.calls.append(("stop_queue", True))

    def force_stop(self) -> None:
        self.calls.append(("force_stop", True))


def make_data(robot: str, tool: str, point_names: list[str]) -> tuple[dict, dict]:
    config = {"robots": {robot: {"end_effector": tool}}}
    points = {
        name: {
            "valid": True,
            "pose": {"x": float(index), "y": 0.0, "z": 0.0, "r": 0.0},
            "speed_percent": 20.0,
        }
        for index, name in enumerate(point_names)
    }
    return config, {"robots": {robot: points}}


class RobotMotionControllerTest(unittest.TestCase):
    def make_controller(self, robot: str = "Dobot_1", tool: str = "gripper"):
        destination = "storage_base_1st" if robot == "Dobot_4" else "assembly_1"
        if robot == "Dobot_4":
            names = [
                "ready", "base_pickup_approach", "base_pickup",
                "jig_pickup_approach", "jig_pickup",
                f"{destination}_approach", destination,
            ]
        else:
            names = [
                "ready", "pickup_approach", "pickup",
                f"{destination}_approach", destination,
            ]
        config, teaching = make_data(robot, tool, names)
        worker = FakeWorker()
        controller = RobotMotionController(
            robot, worker, config=config, teaching_data=teaching
        )
        # 자동 테스트에서는 설정된 안정 시간을 실제로 기다리지 않고 순서만 검사한다.
        controller._wait_safely = lambda _seconds: None
        return controller, worker, destination

    def make_dobot_3_controller(self):
        """두 개의 픽업 접근점을 사용하는 Dobot_3 시험 자료를 만든다."""
        names = [
            "ready", "pickup_approach_1", "pickup_approach_2", "pickup",
            "assembly_1_approach", "assembly_1",
        ]
        config, teaching = make_data("Dobot_3", "suction", names)
        worker = FakeWorker()
        controller = RobotMotionController(
            "Dobot_3", worker, config=config, teaching_data=teaching
        )
        controller._wait_safely = lambda _seconds: None
        return controller, worker

    def test_preview_does_not_move_robot(self) -> None:
        controller, worker, destination = self.make_controller()
        preview = controller.preview(controller.build_cycle_plan(destination))
        # Dobot_1은 픽업 상승 후 ready를 한 번 더 경유하므로 총 13단계이다.
        self.assertEqual(len(preview), 13)
        self.assertEqual(worker.calls, [])

    def test_gripper_cycle_uses_safe_approach_order(self) -> None:
        controller, worker, destination = self.make_controller()
        controller.execute(controller.build_cycle_plan(destination))
        kinds = [call[0] for call in worker.calls]
        self.assertEqual(kinds, [
            "move", "move", "gripper", "move", "move", "move",
            "move", "gripper", "gripper", "move", "move",
        ])
        self.assertEqual(worker.calls[2], ("gripper", "close"))
        self.assertEqual(worker.calls[7], ("gripper", "open"))
        self.assertEqual(worker.calls[8], ("gripper", "disable"))

    def test_cycle_speed_limit_changes_between_manual_and_automatic(self) -> None:
        """저장 속도 20%는 수동 10%, 자동 20%로 각각 제한되어야 한다."""
        controller, _worker, destination = self.make_controller()
        manual_results = controller.execute(controller.build_cycle_plan(destination))
        manual_speeds = [
            result["options"]["speed_percent"]
            for result in manual_results
            if "options" in result
        ]
        self.assertTrue(manual_speeds)
        self.assertTrue(all(
            speed == MANUAL_CYCLE_MAX_PTP_SPEED_PERCENT for speed in manual_speeds
        ))

        controller.set_cycle_speed_limit(AUTOMATIC_CYCLE_MAX_PTP_SPEED_PERCENT)
        automatic_results = controller.execute(controller.build_cycle_plan(destination))
        automatic_speeds = [
            result["options"]["speed_percent"]
            for result in automatic_results
            if "options" in result
        ]
        self.assertTrue(automatic_speeds)
        self.assertTrue(all(speed == 20.0 for speed in automatic_speeds))

    def test_gripper_plan_waits_after_close_and_open(self) -> None:
        """닫기·열기 직후에는 현재 설정값만큼 안정 대기가 있어야 한다."""
        controller, _worker, destination = self.make_controller()
        steps = controller.build_cycle_plan(destination)
        compact = [(step.kind, step.value) for step in steps]

        close_index = compact.index(("tool", "close"))
        open_index = compact.index(("tool", "open"))
        expected_wait = ("wait", str(GRIPPER_SETTLE_SECONDS))
        self.assertEqual(compact[close_index + 1], expected_wait)
        self.assertEqual(compact[open_index + 1], expected_wait)
        self.assertEqual(compact[open_index + 2], ("tool", "disable"))

    def test_suction_cycle_uses_on_and_off(self) -> None:
        controller, worker = self.make_dobot_3_controller()
        controller.execute(controller.build_cycle_plan("assembly_1"))
        self.assertIn(("suction", "on"), worker.calls)
        self.assertIn(("suction", "off"), worker.calls)
        self.assertNotIn(("suction", "disable"), worker.calls)

    def test_operator_ready_allows_cycle_without_home_motion(self) -> None:
        controller, worker, destination = self.make_controller()

        controller.accept_operator_ready()
        self.assertEqual(worker.calls, [])
        controller.run_cycle(destination)

        self.assertNotIn(("home", True), worker.calls)
        self.assertEqual(controller.waiting_point, "ready")

    def test_suction_waits_after_turning_on(self) -> None:
        """흡착 ON 직후에는 설정된 시간 동안 흡착을 유지해야 한다."""
        controller, _worker = self.make_dobot_3_controller()
        compact = [
            (step.kind, step.value)
            for step in controller.build_cycle_plan("assembly_1")
        ]

        suction_on = compact.index(("tool", "on"))
        self.assertEqual(
            compact[suction_on + 1], ("wait", str(SUCTION_SETTLE_SECONDS))
        )

    def test_missing_point_blocks_before_any_motion(self) -> None:
        controller, worker, destination = self.make_controller()
        controller.points["pickup"]["valid"] = False
        with self.assertRaises(TeachingPointError):
            controller.execute(controller.build_cycle_plan(destination))
        self.assertEqual(worker.calls, [])

    def test_wrong_destination_is_rejected(self) -> None:
        controller, _worker, _destination = self.make_controller()
        with self.assertRaises(MotionControllerError):
            controller.build_cycle_plan("return_1")

    def test_initialize_runs_mechanical_then_user_ready(self) -> None:
        controller, worker, _destination = self.make_controller()
        controller.initialize(mechanical_home=True)
        self.assertEqual(worker.calls[0], ("home", True))
        self.assertEqual(worker.calls[1][0], "move")
        self.assertEqual(controller.waiting_point, "ready")

    def test_cycle_requires_initial_home_wait(self) -> None:
        """초기 READY 대기를 확인하지 않은 상태에서는 첫 공정을 차단한다."""
        controller, worker, destination = self.make_controller()

        with self.assertRaises(MotionControllerError):
            controller.run_cycle(destination)

        self.assertEqual(worker.calls, [])

    def test_cycle_finishes_at_home_wait(self) -> None:
        """모든 공정이 끝나면 다음 PLC START를 사용자 READY에서 기다린다."""
        controller, _worker, destination = self.make_controller()
        controller.initialize(mechanical_home=False)

        controller.run_cycle(destination)

        self.assertEqual(controller.waiting_point, "ready")

    def test_dobot_3_skips_outer_approach_on_pickup_entry(self) -> None:
        """Dobot_3은 픽업 진입에서 2번을 생략하고 복귀 시에는 경유한다."""
        controller, _worker = self.make_dobot_3_controller()

        steps = controller.build_cycle_plan("assembly_1")
        moved = [step.value for step in steps if step.kind == "move"]

        self.assertEqual(
            moved,
            [
                "pickup_approach_1", "pickup",
                "pickup_approach_1", "pickup_approach_2",
                "assembly_1_approach", "assembly_1", "assembly_1_approach",
                "ready",
            ],
        )

    def test_dobot_1_passes_ready_between_pickup_and_assembly(self) -> None:
        """Dobot_1은 픽업 상승 후 ready를 거쳐 조립 상승점으로 이동해야 한다."""
        controller, _worker, _destination = self.make_controller("Dobot_1")

        moved = [
            step.value for step in controller.build_cycle_plan("assembly_1")
            if step.kind == "move"
        ]

        self.assertEqual(moved, [
            "pickup_approach", "pickup", "pickup_approach",
            "ready", "assembly_1_approach", "assembly_1",
            "assembly_1_approach", "ready",
        ])

    def test_dobot_2_uses_safe_point_both_directions(self) -> None:
        """두 조립 위치 모두 픽업→조립과 조립→대기 경로에서 안전점을 거친다."""
        names = [
            "ready", "safe_point", "pickup_approach", "pickup",
            "assembly_1_approach", "assembly_1_approach2", "assembly_1",
            "assembly_2_approach", "assembly_2",
        ]
        config, teaching = make_data("Dobot_2", "gripper", names)
        controller = RobotMotionController(
            "Dobot_2", FakeWorker(), config=config, teaching_data=teaching
        )

        for destination in ("assembly_1", "assembly_2"):
            moved = [
                step.value for step in controller.build_cycle_plan(destination)
                if step.kind == "move"
            ]
            self.assertEqual(moved, [
                "pickup_approach", "pickup", "pickup_approach",
                "safe_point",
                *(["assembly_2_approach"] if destination == "assembly_1" else []),
                f"{destination}_approach", destination,
                ("assembly_1_approach2" if destination == "assembly_1"
                 else f"{destination}_approach"), "safe_point", "ready",
            ])

    def test_dobot_2_blocks_first_assembly_until_return_point_is_taught(self) -> None:
        names = [
            "ready", "safe_point", "pickup_approach", "pickup",
            "assembly_1_approach", "assembly_1_approach2", "assembly_1",
            "assembly_2_approach", "assembly_2",
        ]
        config, teaching = make_data("Dobot_2", "gripper", names)
        teaching["robots"]["Dobot_2"]["assembly_1_approach2"]["valid"] = False
        worker = FakeWorker()
        controller = RobotMotionController(
            "Dobot_2", worker, config=config, teaching_data=teaching
        )

        with self.assertRaises(TeachingPointError):
            controller.execute(controller.build_cycle_plan("assembly_1"))

        self.assertEqual(worker.calls, [])

    def test_dobot_4_main_product_uses_base_pickup(self) -> None:
        controller, _worker, _destination = self.make_controller("Dobot_4")
        moved = [
            step.value for step in controller.build_cycle_plan("storage_base_1st")
            if step.kind == "move"
        ]
        self.assertEqual(moved[:3], [
            "base_pickup_approach", "base_pickup", "base_pickup_approach"
        ])

    def test_dobot_4_stacked_main_uses_level_specific_approach(self) -> None:
        names = [
            "ready", "base_pickup_approach", "base_pickup",
            "storage_base_1st_approach", "storage_base_1st",
            "storage_base_2nd_approach", "storage_base_2nd",
            "storage_base_3rd_approach", "storage_base_3rd",
        ]
        config, teaching = make_data("Dobot_4", "gripper", names)
        controller = RobotMotionController(
            "Dobot_4", FakeWorker(), config=config, teaching_data=teaching
        )
        for level in ("1st", "2nd", "3rd"):
            destination = f"storage_base_{level}"
            approach = f"{destination}_approach"
            moved = [
                step.value for step in controller.build_cycle_plan(destination)
                if step.kind == "move"
            ]
            self.assertEqual(moved, [
                "base_pickup_approach", "base_pickup", "base_pickup_approach",
                approach, destination, approach, "ready",
            ])

    def test_dobot_4_lamp_and_seat_use_jig_pickup(self) -> None:
        names = [
            "ready", "base_pickup_approach", "base_pickup",
            "jig_pickup_approach", "jig_pickup",
            "return_Lamp_a_approach", "return_Lamp_a",
            "return_Lamp_b_approach", "return_Lamp_b",
            "return_Seat_a_approach", "return_Seat_a",
            "return_Seat_b_approach", "return_Seat_b",
        ]
        config, teaching = make_data("Dobot_4", "gripper", names)
        controller = RobotMotionController(
            "Dobot_4", FakeWorker(), config=config, teaching_data=teaching
        )
        for destination in (
            "return_Lamp_a", "return_Lamp_b", "return_Seat_a", "return_Seat_b"
        ):
            moved = [
                step.value for step in controller.build_cycle_plan(destination)
                if step.kind == "move"
            ]
            self.assertEqual(moved[:3], [
                "jig_pickup_approach", "jig_pickup", "jig_pickup_approach"
            ])
            self.assertEqual(moved[3:5], ["ready", f"{destination}_approach"])

    def test_dobot_4_base_supply_uses_top_stack_and_main_conveyor(self) -> None:
        names = [
            "ready",
            "storage_base_1st_approach", "storage_base_1st",
            "storage_base_2nd_approach", "storage_base_2nd",
            "storage_base_3rd_approach", "storage_base_3rd",
            "main_conveyor_approach", "main_conveyor",
        ]
        config, teaching = make_data("Dobot_4", "gripper", names)
        controller = RobotMotionController(
            "Dobot_4", FakeWorker(), config=config, teaching_data=teaching
        )

        for level in ("1st", "2nd", "3rd"):
            storage = f"storage_base_{level}"
            approach = f"{storage}_approach"
            moved = [
                step.value for step in controller.build_base_supply_plan(storage)
                if step.kind == "move"
            ]
            self.assertEqual(moved, [
                approach, storage, approach,
                "main_conveyor_approach", "main_conveyor",
                "main_conveyor_approach", "ready",
            ])

    def test_emergency_stop_uses_separate_force_stop_path(self) -> None:
        """비상정지는 일반 JOG/Queue 명령보다 강제 정지 경로를 우선해야 한다."""
        controller, worker, _destination = self.make_controller()

        controller.emergency_stop()

        self.assertEqual(worker.calls, [("force_stop", True)])
        with self.assertRaises(MotionControllerError):
            controller.execute(controller.build_initial_plan())


if __name__ == "__main__":
    unittest.main(verbosity=2)
