"""dobot_config.py 설정 관리 기능의 자동 테스트.

실제 Dobot이나 COM 포트에 연결하지 않고 JSON 설정값만 검사한다. 따라서 로봇은
움직이지 않으며, 언제든지 반복 실행할 수 있다.
"""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path


# tests 폴더의 상위 폴더를 Python 모듈 검색 경로에 추가한다.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from robot_control.dobot_config import (
    ConfigError,
    DEFAULT_CONFIG,
    assign_port,
    load_config,
    save_config,
)


class DobotConfigTest(unittest.TestCase):
    """포트와 엔드이펙터 설정이 정확하고 안전하게 저장되는지 검사한다."""

    def test_current_port_and_end_effector_mapping(self) -> None:
        """포트가 바뀌어도 네 로봇의 장치 정보가 서로 구분되는지 확인한다."""
        config = load_config(DEFAULT_CONFIG, require_all_ports=True)

        expected = {
            "Dobot_1": ("gripper", "EXAMPLE_DOBOT_1"),
            "Dobot_2": ("gripper", "EXAMPLE_DOBOT_2"),
            "Dobot_3": ("suction", "EXAMPLE_DOBOT_3"),
            "Dobot_4": ("gripper", "EXAMPLE_DOBOT_4"),
        }

        ports: list[str] = []
        for robot_name, (expected_tool, expected_serial) in expected.items():
            robot = config["robots"][robot_name]
            self.assertRegex(robot["port"].upper(), r"^COM\d+$")
            self.assertEqual(robot["end_effector"], expected_tool)
            self.assertEqual(robot["serial_number"], expected_serial)
            ports.append(robot["port"].upper())
        self.assertEqual(len(ports), len(set(ports)))

    def test_invalid_end_effector_is_rejected(self) -> None:
        """gripper와 suction 이외의 잘못된 값은 저장되지 않아야 한다."""
        config = copy.deepcopy(load_config(DEFAULT_CONFIG))
        config["robots"]["Dobot_1"]["end_effector"] = "unknown_tool"

        with tempfile.TemporaryDirectory() as temporary_directory:
            test_file = Path(temporary_directory) / "robots.json"
            with self.assertRaises(ConfigError):
                save_config(config, test_file)

    def test_mapping_can_be_saved_and_loaded_without_data_loss(self) -> None:
        """저장 후 다시 읽어도 포트와 엔드이펙터가 변하지 않는지 확인한다."""
        original = load_config(DEFAULT_CONFIG, require_all_ports=True)

        with tempfile.TemporaryDirectory() as temporary_directory:
            test_file = Path(temporary_directory) / "robots.json"
            save_config(original, test_file)
            restored = load_config(test_file, require_all_ports=True)

        self.assertEqual(restored, original)

    def test_duplicate_port_is_rejected(self) -> None:
        """두 로봇에 같은 COM 포트를 등록하면 오류가 발생해야 한다."""
        original = load_config(DEFAULT_CONFIG, require_all_ports=True)

        with tempfile.TemporaryDirectory() as temporary_directory:
            test_file = Path(temporary_directory) / "robots.json"
            save_config(original, test_file)

            # 현재 Dobot_1 포트를 읽어 Dobot_2에도 등록하려고 시도한다.
            with self.assertRaises(ConfigError):
                assign_port("Dobot_2", original["robots"]["Dobot_1"]["port"], test_file)

    def test_duplicate_check_ignores_letter_case(self) -> None:
        """'COM12'와 'com12'도 같은 포트로 판단해야 한다."""
        original = load_config(DEFAULT_CONFIG, require_all_ports=True)

        with tempfile.TemporaryDirectory() as temporary_directory:
            test_file = Path(temporary_directory) / "robots.json"
            save_config(original, test_file)

            duplicate = original["robots"]["Dobot_1"]["port"].lower()
            with self.assertRaises(ConfigError):
                assign_port("Dobot_2", duplicate, test_file)

    def test_failed_duplicate_assignment_keeps_original_file(self) -> None:
        """중복 등록 실패 후에도 기존의 정상 설정이 그대로 남아야 한다."""
        original = load_config(DEFAULT_CONFIG, require_all_ports=True)

        with tempfile.TemporaryDirectory() as temporary_directory:
            test_file = Path(temporary_directory) / "robots.json"
            save_config(original, test_file)

            with self.assertRaises(ConfigError):
                assign_port("Dobot_4", original["robots"]["Dobot_1"]["port"], test_file)

            # 실패한 값을 파일에 쓰지 않았는지 다시 읽어서 확인한다.
            restored = load_config(test_file, require_all_ports=True)

        self.assertEqual(restored, original)

    def test_duplicate_serial_number_is_rejected(self) -> None:
        """서로 다른 로봇에 같은 시리얼번호가 저장되면 오류가 발생해야 한다."""
        config = copy.deepcopy(load_config(DEFAULT_CONFIG))
        config["robots"]["Dobot_2"]["serial_number"] = config["robots"]["Dobot_1"][
            "serial_number"
        ]

        with tempfile.TemporaryDirectory() as temporary_directory:
            test_file = Path(temporary_directory) / "robots.json"
            with self.assertRaises(ConfigError):
                save_config(config, test_file)


if __name__ == "__main__":
    # verbosity=2는 어떤 테스트가 성공했는지 이름까지 자세히 보여준다.
    unittest.main(verbosity=2)
