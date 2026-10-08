"""저속 조그 안전 제한을 실제 로봇 없이 검사한다."""

from __future__ import annotations

import unittest

from robot_control.dobot_jog import JogRequest


class DobotJogTest(unittest.TestCase):
    """축 변환과 시간·속도 제한이 우회되지 않는지 확인한다."""

    def test_all_axis_direction_codes(self) -> None:
        expected = {
            ("x", "+"): 1, ("x", "-"): 2,
            ("y", "+"): 3, ("y", "-"): 4,
            ("z", "+"): 5, ("z", "-"): 6,
            ("r", "+"): 7, ("r", "-"): 8,
            ("j1", "+"): 1, ("j1", "-"): 2,
            ("j2", "+"): 3, ("j2", "-"): 4,
            ("j3", "+"): 5, ("j3", "-"): 6,
            ("j4", "+"): 7, ("j4", "-"): 8,
        }
        for key, code in expected.items():
            jog = JogRequest.from_payload(
                {"axis": key[0], "direction": key[1], "duration": 0.1, "speed_percent": 5}
            )
            self.assertEqual(jog.command_code, code)
            self.assertEqual(jog.is_joint, key[0].startswith("j"))

    def test_too_long_duration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            JogRequest.from_payload(
                {"axis": "x", "direction": "+", "duration": 1.0, "speed_percent": 5}
            )

    def test_high_speed_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            JogRequest.from_payload(
                {"axis": "z", "direction": "+", "duration": 0.1, "speed_percent": 101}
            )

    def test_full_speed_is_allowed(self) -> None:
        """사용자가 요청한 100% 설정은 허용 범위의 최댓값이다."""
        jog = JogRequest.from_payload(
            {"axis": "r", "direction": "+", "duration": 0.1, "speed_percent": 100}
        )
        self.assertEqual(jog.speed_percent, 100.0)

    def test_unknown_axis_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            JogRequest.from_payload(
                {"axis": "joint5", "direction": "+", "duration": 0.1, "speed_percent": 5}
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
