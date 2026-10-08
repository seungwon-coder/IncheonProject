"""Dobot_4 차량 하부 적층 창고의 재고를 안전하게 관리한다."""

from __future__ import annotations

import json
import threading
from pathlib import Path


DEFAULT_INVENTORY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "main_warehouse_inventory.json"
)


class MainWarehouseInventoryError(RuntimeError):
    """창고가 비었거나 가득 차 재고 작업을 수행할 수 없을 때 발생한다."""


class MainWarehouseInventory:
    """최대 3단 적층 창고의 수량과 사용할 티칭 포인트를 결정한다."""

    capacity = 3

    def __init__(self, path: str | Path = DEFAULT_INVENTORY_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._count = self._load()

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    @property
    def status_text(self) -> str:
        count = self.count
        if count == 0:
            return "비어 있음"
        if count == self.capacity:
            return "가득 참"
        return "사용 가능"

    def set_count(self, count: int) -> int:
        """작업자가 확인한 실제 재고를 저장한다."""
        if type(count) is not int or not 0 <= count <= self.capacity:
            raise ValueError("차량 하부 재고는 0~3의 정수여야 합니다.")
        with self._lock:
            self._count = count
            self._save()
            return self._count

    def next_deposit_point(self) -> str:
        """현재 재고 위의 다음 빈 적층 위치를 반환한다(수량은 바꾸지 않음)."""
        with self._lock:
            if self._count >= self.capacity:
                raise MainWarehouseInventoryError("차량 하부 창고가 가득 찼습니다.")
            return self._point_for_level(self._count + 1)

    def current_pickup_point(self) -> str:
        """창고 최상단 제품 위치를 반환한다(수량은 바꾸지 않음)."""
        with self._lock:
            if self._count <= 0:
                raise MainWarehouseInventoryError("차량 하부 창고가 비었습니다.")
            return self._point_for_level(self._count)

    def confirm_deposit(self) -> int:
        """창고 입고 사이클 정상 완료 후 재고를 1 증가시킨다."""
        return self.set_count(self.count + 1)

    def confirm_supply(self) -> int:
        """컨베이어 공급 사이클 정상 완료 후 재고를 1 감소시킨다."""
        return self.set_count(self.count - 1)

    @staticmethod
    def _point_for_level(level: int) -> str:
        suffix = {1: "1st", 2: "2nd", 3: "3rd"}[level]
        return f"storage_base_{suffix}"

    def _load(self) -> int:
        if not self.path.exists():
            return 0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            count = data["count"]
            if type(count) is not int or not 0 <= count <= self.capacity:
                raise ValueError
            return count
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            raise MainWarehouseInventoryError(
                f"차량 하부 재고 파일을 읽을 수 없습니다: {self.path}"
            ) from exc

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"count": self._count, "capacity": self.capacity}, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
