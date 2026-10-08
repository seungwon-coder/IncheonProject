"""알람 표시 변환을 실제 로봇 없이 검사한다."""

from __future__ import annotations

import unittest

from robot_control.dobot_alarm import alarm_details


class DobotAlarmTest(unittest.TestCase):
    def test_alarm_codes_include_hex(self) -> None:
        self.assertEqual(
            alarm_details([0, 16, 73]),
            [
                {"code": 0, "hex": "0x00"},
                {"code": 16, "hex": "0x10"},
                {"code": 73, "hex": "0x49"},
            ],
        )

    def test_negative_alarm_code_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            alarm_details([-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
