"""8단계 PC 모의 신호를 실제 Dobot 없이 한 번에 시연한다."""

from __future__ import annotations

from communication.mock_signal_source import MockSignalSource
from robot_control.process_state_machine import ProcessSignals, RobotProcessStateMachine


class NoRobotController:
    """목적지만 출력하고 로봇 명령을 전혀 보내지 않는 시험용 제어기."""

    def __init__(self) -> None:
        self.destinations: list[str] = []

    def initialize(self, mechanical_home: bool = False) -> list[dict]: return []
    def run_cycle(self, destination: str) -> list[dict]:
        self.destinations.append(destination)
        return [{"simulated_destination": destination}]


def simulate(robot: str, updates: list[dict]) -> list[str]:
    """입력 순서대로 신호를 넣고 발생한 모의 목적지를 반환한다."""
    source = MockSignalSource()
    controller = NoRobotController()
    machine = RobotProcessStateMachine(robot, controller)
    machine.initialize(mechanical_home=False)
    for change in updates:
        source.update(robot, **change)
        source.dispatch(robot, machine)
    return controller.destinations


def main() -> None:
    scenarios = {
        "Dobot_1 공급→비전": simulate(
            "Dobot_1", [{"supply_complete": True}, {"vision_ok": True}]
        ),
        "Dobot_2 두 번 조립": simulate(
            "Dobot_2",
            [
                {"work_count": 2, "vision_ok": True},
                {"supply_complete": True},
                {"supply_complete": False},
                {"supply_complete": True},
            ],
        ),
        "Dobot_3 AGV→비전": simulate(
            "Dobot_3", [{"agv_supply_complete": True}, {"vision_ok": True}]
        ),
        "Dobot_4 Seat_b": simulate(
            "Dobot_4", [{"product_type": "Seat_b"}, {"product_arrived": True}]
        ),
    }
    for name, destinations in scenarios.items():
        print(f"{name}: {destinations}")
    print("모의 신호 시험 완료: 실제 Dobot에는 연결하지 않았습니다.")


if __name__ == "__main__":
    main()
