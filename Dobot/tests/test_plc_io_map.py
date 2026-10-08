"""사용자가 확정한 PLC 주소가 논리 태그와 정확히 연결되는지 검사한다."""

import unittest

from communication.plc_io_map import PLC_MANAGED_COUNTERS, addresses_for


class PlcIoMapTest(unittest.TestCase):
    def test_each_robot_has_start_and_four_status_addresses(self) -> None:
        for number in range(1, 5):
            robot = f"Dobot_{number}"
            tags = {item.logical_tag for item in addresses_for(robot)}
            self.assertTrue({
                "command.start", "status.busy", "status.done",
                "status.error", "status.ready",
            }.issubset(tags))

    def test_dobot_2_work_count_is_read_from_d1011(self) -> None:
        mapping = {item.address: item for item in addresses_for("Dobot_2")}
        self.assertEqual(mapping["D1011"].logical_tag, "input.work_count")
        self.assertEqual(mapping["D1011"].direction, "PLC_TO_PYTHON")

    def test_d1012_is_owned_by_plc_not_python_output(self) -> None:
        self.assertIn("D1012", PLC_MANAGED_COUNTERS)
        all_python_outputs = {
            item.address
            for number in range(1, 5)
            for item in addresses_for(f"Dobot_{number}")
            if item.direction == "PYTHON_TO_PLC"
        }
        self.assertNotIn("D1012", all_python_outputs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
