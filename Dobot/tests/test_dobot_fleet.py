"""Fleet가 로봇별 오류를 분리하는지 실제 Dobot 없이 검사한다."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from robot_control.dobot_fleet import DobotFleet


class DobotFleetTest(unittest.TestCase):
    """한 Worker 예외 후에도 다음 Worker가 호출되는지 확인한다."""

    def test_one_status_failure_does_not_stop_other_workers(self) -> None:
        """Dobot_1 실패와 관계없이 Dobot_2~4 상태도 모두 읽어야 한다."""
        fleet = DobotFleet()
        fleet.workers["Dobot_1"].request_with_recovery = Mock(
            side_effect=TimeoutError("가상 통신 장애")
        )
        for name in ("Dobot_2", "Dobot_3", "Dobot_4"):
            fleet.workers[name].request_with_recovery = Mock(
                return_value={"robot": name}
            )

        results = fleet.read_all_status()

        self.assertFalse(results["Dobot_1"].ok)
        for name in ("Dobot_2", "Dobot_3", "Dobot_4"):
            self.assertTrue(results[name].ok)
            fleet.workers[name].request_with_recovery.assert_called_once()

    def test_close_failure_does_not_stop_other_workers(self) -> None:
        """한 Worker 종료 실패가 나머지 Worker 종료를 막지 않아야 한다."""
        fleet = DobotFleet()
        fleet.workers["Dobot_1"].close = Mock(side_effect=RuntimeError("가상 종료 장애"))
        for name in ("Dobot_2", "Dobot_3", "Dobot_4"):
            fleet.workers[name].close = Mock()

        results = fleet.close_all()

        self.assertFalse(results["Dobot_1"].ok)
        for name in ("Dobot_2", "Dobot_3", "Dobot_4"):
            self.assertTrue(results[name].ok)
            fleet.workers[name].close.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
