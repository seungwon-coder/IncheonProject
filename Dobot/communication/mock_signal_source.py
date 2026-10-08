"""서버 구축 전 PLC·비전·AGV 신호를 PC에서 모의 입력한다.

GUI와 OPC-DA는 신호를 가져오는 방법만 다르다. 둘 다 update()에 같은 필드 이름을
넘기면 7단계 상태 머신에는 동일한 ProcessSignals 객체가 전달된다.
"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from robot_control.dobot_config import ROBOT_NAMES
from robot_control.process_state_machine import ProcessSignals, RobotProcessStateMachine


SIGNAL_NAMES = {item.name for item in fields(ProcessSignals)}
# 로봇별로 새 공정을 발생시키는 기준 신호이다. 이 신호가 OFF로 돌아와야 재기동된다.
LEGACY_START_SIGNALS = {
    "Dobot_1": "supply_complete",
    "Dobot_2": "supply_complete",
    "Dobot_3": "agv_supply_complete",
    "Dobot_4": "product_arrived",
}


class MockSignalError(ValueError):
    """알 수 없는 로봇이나 잘못된 모의 신호를 입력했을 때 발생한다."""


class MockSignalSource:
    """네 로봇의 모의 신호와 중복 기동 방지 상태를 메모리에 보관한다."""

    def __init__(self) -> None:
        self._signals = {name: ProcessSignals() for name in ROBOT_NAMES}
        self._revision = {name: 0 for name in ROBOT_NAMES}
        self._consumed_revision = {name: 0 for name in ROBOT_NAMES}
        self._armed = {name: True for name in ROBOT_NAMES}

    def update(self, robot_name: str, **changes: Any) -> bool:
        """GUI에서 바꾼 값만 갱신하고 실제 변화가 있었는지 반환한다."""
        self._check_robot(robot_name)
        unknown = set(changes) - SIGNAL_NAMES
        if unknown:
            raise MockSignalError(f"알 수 없는 모의 신호: {', '.join(sorted(unknown))}")
        values = asdict(self._signals[robot_name])
        values.update(changes)
        try:
            updated = ProcessSignals(**values)
        except (TypeError, ValueError) as exc:
            raise MockSignalError(str(exc)) from exc
        if updated == self._signals[robot_name]:
            # 같은 값을 반복해서 쓰는 것은 새 PLC 신호로 취급하지 않는다.
            return False
        self._signals[robot_name] = updated
        self._revision[robot_name] += 1
        if not self._start_is_on(robot_name, updated):
            self._armed[robot_name] = True
        return True

    def snapshot(self, robot_name: str) -> ProcessSignals:
        """현재 모의 신호 전체를 변경하지 않고 읽는다."""
        self._check_robot(robot_name)
        return self._signals[robot_name]

    def consume_if_changed(self, robot_name: str) -> ProcessSignals | None:
        """마지막 확인 이후 값이 달라진 경우에만 새 신호 묶음을 반환한다."""
        self._check_robot(robot_name)
        revision = self._revision[robot_name]
        if revision == self._consumed_revision[robot_name]:
            return None
        self._consumed_revision[robot_name] = revision
        return self._signals[robot_name]

    def dispatch(
        self, robot_name: str, machine: RobotProcessStateMachine
    ) -> list[dict[str, Any]] | None:
        """새 모의 입력을 상태 머신에 한 번 전달하고 중복 기동을 잠근다."""
        signals = self.consume_if_changed(robot_name)
        if signals is None or not self._armed[robot_name]:
            return None
        result = machine.process(signals)
        if result is not None:
            # 공정이 시작되면 기준 신호가 OFF가 될 때까지 같은 공정을 다시 막는다.
            self._armed[robot_name] = False
        return result

    def reset(self, robot_name: str) -> None:
        """선택 로봇의 모든 모의 입력을 초기값으로 되돌린다."""
        self._check_robot(robot_name)
        if self._signals[robot_name] != ProcessSignals():
            self._signals[robot_name] = ProcessSignals()
            self._revision[robot_name] += 1
        self._armed[robot_name] = True

    @staticmethod
    def _check_robot(robot_name: str) -> None:
        if robot_name not in ROBOT_NAMES:
            raise MockSignalError(f"등록되지 않은 로봇: {robot_name}")

    @staticmethod
    def _start_is_on(robot_name: str, signals: ProcessSignals) -> bool:
        """새 PLC START 또는 이전 GUI 시험용 기준 신호의 ON 여부를 반환한다."""
        return signals.start or bool(getattr(signals, LEGACY_START_SIGNALS[robot_name]))
