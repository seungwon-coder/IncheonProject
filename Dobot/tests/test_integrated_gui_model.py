"""창과 장비 없이 9단계 통합 GUI 모델을 검사한다."""

import unittest

from integrated_gui.integrated_gui_model import IntegratedGuiModel


class IntegratedGuiModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = IntegratedGuiModel()

    def test_initialize_all_makes_all_robots_wait(self) -> None:
        self.model.initialize_all()
        for number in range(1, 5):
            self.assertEqual(self.model.state(f"Dobot_{number}"), "waiting")

    def test_dobot_1_mock_cycle(self) -> None:
        self.model.initialize("Dobot_1")
        self.model.update_signals(
            "Dobot_1", supply_complete=True, vision_ok=True
        )
        result = self.model.execute("Dobot_1")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["command"], "dry_run_cycle")
        self.assertIn("포인트 이동: pickup", result[0]["plan"])
        self.assertEqual(
            self.model.controllers["Dobot_1"].executed_destinations,
            ["assembly_1"],
        )

    def test_dobot_4_product_type_selects_return_destination(self) -> None:
        self.model.initialize("Dobot_4")
        self.model.update_signals(
            "Dobot_4", product_arrived=True, product_type="Seat_b"
        )
        self.model.execute("Dobot_4")
        self.assertEqual(
            self.model.controllers["Dobot_4"].executed_destinations,
            ["return_Seat_b"],
        )

    def test_emergency_and_reset(self) -> None:
        self.model.initialize("Dobot_3")
        self.model.emergency_stop("Dobot_3")
        self.assertEqual(self.model.state("Dobot_3"), "error")
        self.model.reset("Dobot_3")
        self.assertEqual(self.model.state("Dobot_3"), "waiting")


if __name__ == "__main__":
    unittest.main()
