"""엔드이펙터 공통 명령 변환을 실제 출력 없이 검사한다."""

from __future__ import annotations

import unittest

from robot_control.dobot_end_effector import EndEffectorRequest


class EndEffectorTest(unittest.TestCase):
    def test_gripper_actions(self) -> None:
        self.assertEqual(EndEffectorRequest.from_payload(
            {"tool_type": "gripper", "action": "open"}).sdk_values, (True, False))
        self.assertEqual(EndEffectorRequest.from_payload(
            {"tool_type": "gripper", "action": "close"}).sdk_values, (True, True))

    def test_suction_actions(self) -> None:
        self.assertEqual(EndEffectorRequest.from_payload(
            {"tool_type": "suction", "action": "on"}).sdk_values, (True, True))
        self.assertEqual(EndEffectorRequest.from_payload(
            {"tool_type": "suction", "action": "off"}).sdk_values, (True, False))

    def test_wrong_action_for_tool_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EndEffectorRequest.from_payload(
                {"tool_type": "suction", "action": "close"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
