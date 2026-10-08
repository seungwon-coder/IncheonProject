"""티칭 좌표 저장과 필수 포인트 검사를 실제 로봇 없이 시험한다."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from robot_control.dobot_config import load_config
from teaching.teaching_points import (
    TeachingPointError,
    load_teaching_data,
    missing_points,
    new_teaching_data,
    save_teaching_data,
    teach_point,
)


class TeachingPointsTest(unittest.TestCase):
    def test_new_file_contains_all_required_points_as_invalid(self) -> None:
        """로봇 설정의 최신 포인트 목록이 빠짐없이 미등록 상태로 생성돼야 한다."""
        config = load_config()
        data = new_teaching_data()
        expected_total = 0
        for robot_name, robot in config["robots"].items():
            expected_names = set(robot["teaching_points"])
            self.assertEqual(set(data["robots"][robot_name]), expected_names)
            self.assertTrue(
                all(not point["valid"] for point in data["robots"][robot_name].values())
            )
            expected_total += len(expected_names)
        self.assertEqual(len(missing_points(data)), expected_total)

    def test_teach_point_records_pose_speed_time_and_validity(self) -> None:
        data = teach_point(
            new_teaching_data(),
            "Dobot_1",
            "ready",
            {"x": 150, "y": 0, "z": 50, "r": 0},
            5,
        )
        point = data["robots"]["Dobot_1"]["ready"]
        self.assertTrue(point["valid"])
        self.assertEqual(point["pose"]["x"], 150.0)
        self.assertEqual(point["speed_percent"], 5.0)
        self.assertTrue(point["updated_at"])

    def test_unknown_point_is_rejected(self) -> None:
        with self.assertRaises(TeachingPointError):
            teach_point(
                new_teaching_data(), "Dobot_1", "unknown",
                {"x": 0, "y": 0, "z": 0, "r": 0}, 5,
            )

    def test_atomic_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "points.json"
            original = new_teaching_data()
            save_teaching_data(original, path)
            self.assertEqual(load_teaching_data(path), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
