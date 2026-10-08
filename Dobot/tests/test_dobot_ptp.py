"""PTP 시험 이동의 안전 제한을 실제 로봇 없이 확인한다."""

from __future__ import annotations

import unittest

from robot_control.dobot_errors import MAX_COMMAND_TIMEOUT
from robot_control.dobot_ptp import MAX_TIMEOUT, MIN_TIMEOUT, PTP_RESPONSE_MARGIN_SECONDS, PTPRequest


class DobotPTPTest(unittest.TestCase):
    def make_request(self, **changes: float) -> PTPRequest:
        payload = {
            "x": 100, "y": 100, "z": 50, "r": 0,
            "speed_percent": 5, "timeout": 10, "tolerance": 0.25,
        }
        payload.update(changes)
        return PTPRequest.from_payload(payload)

    def test_small_move_is_allowed(self) -> None:
        self.make_request(x=104, r=4).validate_small_move(
            {"x": 100, "y": 100, "z": 50, "r": 0}
        )

    def test_large_xyz_move_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.make_request(x=106).validate_small_move(
                {"x": 100, "y": 100, "z": 50, "r": 0}
            )

    def test_confirmed_teaching_move_allows_large_distance(self) -> None:
        """GUI에서 사용자가 확인한 이동만 기존 5 mm 제한을 해제한다."""
        self.make_request(x=250, allow_large_move=True).validate_small_move(
            {"x": 100, "y": 100, "z": 50, "r": 0}
        )

    def test_large_move_flag_must_be_boolean(self) -> None:
        with self.assertRaises(ValueError):
            self.make_request(allow_large_move=1)

    def test_high_speed_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.make_request(speed_percent=101)

    def test_fractional_speed_limits(self) -> None:
        for speed in (5, 25, 50, 100):
            self.assertEqual(self.make_request(speed_percent=speed).speed_percent, speed)
        for speed in (0, 4.9, 100.1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.make_request(speed_percent=speed)

    def test_timeout_boundaries_match_error_message(self) -> None:
        """허용 상·하한과 화면에 표시되는 범위가 서로 어긋나지 않아야 한다."""
        self.assertEqual(self.make_request(timeout=MIN_TIMEOUT).timeout, MIN_TIMEOUT)
        self.assertEqual(self.make_request(timeout=MAX_TIMEOUT).timeout, MAX_TIMEOUT)
        with self.assertRaisesRegex(ValueError, "1~120초"):
            self.make_request(timeout=MAX_TIMEOUT + 0.1)

    def test_worker_timeout_includes_ptp_response_margin(self) -> None:
        """최대 PTP 시간에도 Worker 결과 전달 여유 2초가 보장돼야 한다."""
        self.assertLessEqual(
            MAX_TIMEOUT + PTP_RESPONSE_MARGIN_SECONDS,
            MAX_COMMAND_TIMEOUT,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
