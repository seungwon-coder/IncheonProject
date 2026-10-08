"""티칭 GUI의 사이클 포인트 순서가 실제 공정 계획과 일치하는지 검사한다."""
import unittest
from unittest.mock import Mock

from robot_control.robot_motion_controller import RobotMotionController
from teaching.teaching_gui import TeachingGUI


class TeachingCycleRoutesTest(unittest.TestCase):
    def test_dobot_2_route_includes_safe_point_on_both_sides(self):
        gui = TeachingGUI.__new__(TeachingGUI)
        gui.robot_var = Mock(get=lambda: "Dobot_2")
        gui.cycle_destination_var = Mock(get=lambda: "assembly_2")
        gui.cycle_route_var = Mock()
        gui.cycle_destination_box = {}
        gui.config = {
            "robots": {"Dobot_2": {"end_effector": "gripper"}}
        }
        names = (
            "ready", "safe_point", "pickup_approach", "pickup",
            "assembly_1_approach", "assembly_1_approach2", "assembly_1",
            "assembly_2_approach", "assembly_2",
        )
        gui.points = {"robots": {"Dobot_2": {
            name: {"speed_percent": 30.0, "valid": True,
                   "pose": {"x": 0, "y": 0, "z": 0, "r": 0}}
            for name in names
        }}}
        controller = RobotMotionController(
            "Dobot_2", None, config=gui.config, teaching_data=gui.points
        )
        expected = [step.value for step in controller.build_cycle_plan("assembly_2")
                    if step.kind == "move"]

        gui._refresh_cycle_routes()

        self.assertEqual(gui.cycle_routes["assembly_2"], expected)
        self.assertEqual(expected.count("safe_point"), 2)
        shown = gui.cycle_route_var.set.call_args.args[0]
        self.assertIn("assembly_2 (10%)", shown)
        self.assertEqual(shown.count("safe_point (10%)"), 2)

    def test_dobot_4_shows_all_three_base_supply_routes(self):
        gui = TeachingGUI.__new__(TeachingGUI)
        gui.robot_var = Mock(get=lambda: "Dobot_4")
        gui.cycle_destination_var = Mock(get=lambda: "base_supply_from_3rd")
        gui.cycle_route_var = Mock()
        gui.cycle_destination_box = {}
        names = (
            "ready", "base_pickup_approach", "base_pickup",
            "jig_pickup_approach", "jig_pickup",
            "storage_base_1st_approach", "storage_base_1st",
            "storage_base_2nd_approach", "storage_base_2nd",
            "storage_base_3rd_approach", "storage_base_3rd",
            "main_conveyor_approach", "main_conveyor",
            "return_Lamp_a_approach", "return_Lamp_a",
            "return_Lamp_b_approach", "return_Lamp_b",
            "return_Seat_a_approach", "return_Seat_a",
            "return_Seat_b_approach", "return_Seat_b",
        )
        gui.config = {"robots": {"Dobot_4": {"end_effector": "gripper"}}}
        gui.points = {"robots": {"Dobot_4": {
            name: {"speed_percent": 10.0, "valid": True,
                   "pose": {"x": 0, "y": 0, "z": 0, "r": 0}}
            for name in names
        }}}

        gui._refresh_cycle_routes()

        self.assertIn("base_supply_from_1st", gui.cycle_routes)
        self.assertIn("base_supply_from_2nd", gui.cycle_routes)
        self.assertEqual(gui.cycle_routes["base_supply_from_3rd"], [
            "storage_base_3rd_approach", "storage_base_3rd",
            "storage_base_3rd_approach", "main_conveyor_approach", "main_conveyor",
            "main_conveyor_approach", "ready",
        ])


if __name__ == "__main__":
    unittest.main()
