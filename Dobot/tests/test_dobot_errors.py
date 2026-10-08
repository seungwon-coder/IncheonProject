"""명령 시간 제한과 오류 문구를 실제 로봇 없이 검사한다."""

from __future__ import annotations

import unittest

from robot_control.dobot_errors import (
    MAX_COMMAND_TIMEOUT,
    MIN_COMMAND_TIMEOUT,
    DobotCommandTimeout,
    validate_timeout,
)


class DobotErrorsTest(unittest.TestCase):
    def test_timeout_contains_context(self) -> None:
        error = DobotCommandTimeout("Dobot_1", "get_status", 5.0)
        self.assertIn("Dobot_1", str(error))
        self.assertIn("get_status", str(error))
        self.assertEqual(error.timeout, 5.0)

    def test_timeout_range_is_checked(self) -> None:
        self.assertEqual(validate_timeout(3), 3.0)
        # PTP 최대 120초와 응답 전달 여유 2초까지 허용한다.
        self.assertEqual(validate_timeout(MIN_COMMAND_TIMEOUT), MIN_COMMAND_TIMEOUT)
        self.assertEqual(validate_timeout(MAX_COMMAND_TIMEOUT), MAX_COMMAND_TIMEOUT)
        for invalid in (0, -1, MAX_COMMAND_TIMEOUT + 0.1, "잘못된 값"):
            with self.assertRaises(ValueError):
                validate_timeout(invalid)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main(verbosity=2)
