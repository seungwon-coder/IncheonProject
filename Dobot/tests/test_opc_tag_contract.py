"""실제 KEPServer 없이 OPC-DA 논리 태그 계약을 검사한다."""

import unittest

from robot_control.dobot_config import ROBOT_NAMES
from communication.opc_tag_contract import (
    PRODUCT_TYPE_CODES,
    TagType,
    logical_item_name,
    tags_for,
    validate_value,
)


class OpcTagContractTest(unittest.TestCase):
    def test_every_robot_has_unique_common_handshake_tags(self) -> None:
        for robot in ROBOT_NAMES:
            names = [tag.name for tag in tags_for(robot)]
            self.assertEqual(len(names), len(set(names)))
            self.assertIn("command.start", names)
            self.assertIn("status.done", names)
            self.assertIn("status.error", names)
            self.assertIn("status.ready", names)
            self.assertIn("status.busy", names)
            self.assertNotIn("command.reset", names)
            self.assertNotIn("command.home", names)
            self.assertNotIn("command.pause", names)
            self.assertNotIn("status.error_code", names)

    def test_robot_specific_inputs_match_process_requirements(self) -> None:
        names_2 = {tag.name for tag in tags_for("Dobot_2")}
        names_4 = {tag.name for tag in tags_for("Dobot_4")}
        self.assertIn("input.work_count", names_2)
        self.assertIn("input.product_type", names_4)
        self.assertIn("command.base_start", names_4)
        self.assertIn("status.base_done", names_4)

    def test_product_type_codes_cover_five_destinations(self) -> None:
        self.assertEqual(
            PRODUCT_TYPE_CODES,
            {2: "Lamp_a", 3: "Lamp_b", 4: "Seat_a", 5: "Seat_b", 6: "Main"},
        )

    def test_value_validation_rejects_bool_as_integer(self) -> None:
        tag = next(tag for tag in tags_for("Dobot_2") if tag.name == "input.work_count")
        self.assertIs(tag.data_type, TagType.INT)
        with self.assertRaises(ValueError):
            validate_value(tag, True)

    def test_logical_item_name_is_stable(self) -> None:
        self.assertEqual(
            logical_item_name("Dobot_1", "status.ready"),
            "Dobot.Dobot_1.status.ready",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
