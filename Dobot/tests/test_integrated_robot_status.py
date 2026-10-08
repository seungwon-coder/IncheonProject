"""실제 장비 대신 가짜 Fleet로 통합 GUI 상태 읽기 서비스를 검사한다."""

import unittest

from robot_control.dobot_config import ROBOT_NAMES
from robot_control.dobot_fleet import RobotResult
from integrated_gui.integrated_robot_status import IntegratedRobotStatusService


class FakeFleet:
    def connect_all(self):
        return {name: RobotResult(True, {"port": f"COM{index}"})
                for index, name in enumerate(ROBOT_NAMES, 1)}

    def read_all_status(self):
        return {
            name: RobotResult(True, {
                "pose": {"x": 1.0, "y": 2.0, "z": 3.0, "r": 4.0},
                "joints": [1.0, 2.0, 3.0, 4.0],
                "alarms": [],
            }) for name in ROBOT_NAMES
        }

    def close_all(self):
        return {name: RobotResult(True, "closed") for name in ROBOT_NAMES}


class IntegratedRobotStatusTests(unittest.TestCase):
    def test_connect_refresh_and_close_without_motion_command(self) -> None:
        service = IntegratedRobotStatusService(FakeFleet())
        self.assertTrue(all(item.ok for item in service.connect_all().values()))
        service.refresh_all()
        self.assertEqual(service.statuses["Dobot_1"].pose["z"], 3.0)
        self.assertTrue(service.statuses["Dobot_4"].connected)
        service.close_all()
        self.assertFalse(any(item.connected for item in service.statuses.values()))


if __name__ == "__main__":
    unittest.main()

