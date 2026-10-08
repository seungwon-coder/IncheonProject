"""COM 포트 자동 복구 계산을 실제 로봇 없이 검사한다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from robot_control.dobot_config import ConfigError, DEFAULT_CONFIG, load_config
from robot_control.dobot_port_recovery import match_ports_by_serial


class DobotPortRecoveryTest(unittest.TestCase):
    """시리얼번호를 이용한 포트 매칭 규칙을 검사한다."""

    def test_changed_ports_are_matched_to_correct_robots(self) -> None:
        """네 COM 번호가 바뀌어도 시리얼번호로 원래 로봇을 찾아야 한다."""
        config = load_config(DEFAULT_CONFIG, require_all_ports=True)
        simulated_scan = {
            "COM21": "EXAMPLE_DOBOT_1",  # Dobot_1
            "COM22": "EXAMPLE_DOBOT_2",  # Dobot_2
            "COM23": "EXAMPLE_DOBOT_3",  # Dobot_3
            "COM24": "EXAMPLE_DOBOT_4",  # Dobot_4
        }

        recovered = match_ports_by_serial(config, simulated_scan)

        self.assertEqual(
            recovered,
            {
                "Dobot_1": "COM21",
                "Dobot_2": "COM22",
                "Dobot_3": "COM23",
                "Dobot_4": "COM24",
            },
        )

    def test_missing_robot_stops_recovery(self) -> None:
        """한 대라도 검색되지 않으면 불완전한 매핑을 만들지 않아야 한다."""
        config = load_config(DEFAULT_CONFIG, require_all_ports=True)
        incomplete_scan = {
            "COM21": "EXAMPLE_DOBOT_1",
            "COM22": "EXAMPLE_DOBOT_2",
            "COM23": "EXAMPLE_DOBOT_3",
            # Dobot_4의 시리얼번호는 의도적으로 제외한다.
        }

        with self.assertRaises(ConfigError):
            match_ports_by_serial(config, incomplete_scan)


if __name__ == "__main__":
    unittest.main(verbosity=2)
