"""Dobot_4 차량 하부 적층 재고를 실제 장비 없이 검사한다."""

import tempfile
import unittest
from pathlib import Path

from robot_control.main_warehouse_inventory import (
    MainWarehouseInventory,
    MainWarehouseInventoryError,
)


class MainWarehouseInventoryTests(unittest.TestCase):
    def make_inventory(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return MainWarehouseInventory(Path(temporary.name) / "inventory.json")

    def test_deposit_uses_next_empty_level(self) -> None:
        inventory = self.make_inventory()
        expected = ["storage_base_1st", "storage_base_2nd", "storage_base_3rd"]
        for point in expected:
            self.assertEqual(inventory.next_deposit_point(), point)
            inventory.confirm_deposit()
        self.assertEqual(inventory.count, 3)
        with self.assertRaises(MainWarehouseInventoryError):
            inventory.next_deposit_point()

    def test_supply_uses_current_top_level(self) -> None:
        inventory = self.make_inventory()
        inventory.set_count(3)
        expected = ["storage_base_3rd", "storage_base_2nd", "storage_base_1st"]
        for point in expected:
            self.assertEqual(inventory.current_pickup_point(), point)
            inventory.confirm_supply()
        self.assertEqual(inventory.status_text, "비어 있음")
        with self.assertRaises(MainWarehouseInventoryError):
            inventory.current_pickup_point()

    def test_count_is_persisted(self) -> None:
        inventory = self.make_inventory()
        inventory.set_count(2)
        reloaded = MainWarehouseInventory(inventory.path)
        self.assertEqual(reloaded.count, 2)

    def test_only_zero_to_three_is_allowed(self) -> None:
        inventory = self.make_inventory()
        for invalid in (-1, 4, 1.5, True):
            with self.assertRaises(ValueError):
                inventory.set_count(invalid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
