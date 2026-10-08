"""9단계 통합 GUI가 사용할 공정 관리 모델.

화면 코드와 공정 코드를 분리해 두면 실제 창을 띄우지 않고도 자동 테스트할 수 있다.
현재 기본값은 실제 Dobot에 연결하지 않는 모의 운전용 컨트롤러이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from robot_control.dobot_config import ROBOT_NAMES
from communication.mock_signal_source import MockSignalSource
from communication.process_signal_interface import GuiSignalProvider, ProcessSignalProvider
from robot_control.process_state_machine import ProcessSignals, RobotProcessStateMachine
from robot_control.robot_motion_controller import RobotMotionController


class _NoMotionWorker:
    """경로 검사 중 실수로 장비 명령을 호출하면 즉시 알려 주는 빈 Worker."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"DRY-RUN에서는 Worker 명령을 호출할 수 없습니다: {name}")


@dataclass
class SimulationController:
    """실제 RobotMotionController로 경로만 검사하고 장비에는 보내지 않는다."""

    robot_name: str
    executed_destinations: list[str] = field(default_factory=list)
    paused: bool = False
    emergency: bool = False
    planner: RobotMotionController = field(init=False, repr=False)
    initialized: bool = False

    def __post_init__(self) -> None:
        # 실제 운전과 같은 티칭 포인트·경로 생성 코드를 사용한다. 단, execute()는
        # 호출하지 않으므로 HOME/PTP/엔드이펙터 명령은 절대로 전송되지 않는다.
        self.planner = RobotMotionController(self.robot_name, _NoMotionWorker())

    def initialize(self, mechanical_home: bool = True) -> list[dict[str, Any]]:
        plan = self.planner.preview(self.planner.build_initial_plan())
        self.initialized = True
        return [{
            "command": "dry_run_home",
            "mechanical_home": mechanical_home,
            "plan": plan,
        }]

    def run_cycle(self, destination: str) -> list[dict[str, Any]]:
        if not self.initialized:
            raise RuntimeError("DRY-RUN HOME 경로 검사를 먼저 실행하세요.")
        plan = self.planner.preview(self.planner.build_cycle_plan(destination))
        self.executed_destinations.append(destination)
        return [{
            "command": "dry_run_cycle",
            "destination": destination,
            "plan": plan,
        }]

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def emergency_stop(self) -> None:
        self.emergency = True

    def reset(self) -> None:
        self.paused = False
        self.emergency = False


class IntegratedGuiModel:
    """4대의 상태 머신, 모의 신호, 화면 로그를 한곳에서 관리한다."""

    def __init__(self) -> None:
        self.controllers = {
            name: SimulationController(name) for name in ROBOT_NAMES
        }
        self.machines = {
            name: RobotProcessStateMachine(name, self.controllers[name])
            for name in ROBOT_NAMES
        }
        self.signals = MockSignalSource()
        # GUI 모의 입력도 OPC 입력과 동일한 ProcessSignalProvider 규칙으로 읽는다.
        # 현재 GUI 동작은 그대로 유지되고, 실제 서버 연결 시 공급자만 교체할 수 있다.
        self.signal_provider: ProcessSignalProvider = GuiSignalProvider(self.signals)
        self.logs: list[str] = []
        self._log("통합 GUI가 실제 경로 코드를 사용하는 DRY-RUN 모드로 시작되었습니다.")

    def initialize(self, robot_name: str) -> None:
        """선택 로봇을 모의 HOME 처리하여 공정 대기 상태로 만든다."""
        self._machine(robot_name).initialize(mechanical_home=True)
        self._log(f"{robot_name}: DRY-RUN HOME 경로 검사 완료, 공정 대기")

    def initialize_all(self) -> None:
        for name in ROBOT_NAMES:
            if self.state(name) == "disconnected":
                self.initialize(name)

    def update_signals(self, robot_name: str, **changes: Any) -> bool:
        changed = self.signals.update(robot_name, **changes)
        if changed:
            self._log(f"{robot_name}: 모의 입력 변경 {changes}")
        return changed

    def execute(self, robot_name: str) -> list[dict[str, Any]] | None:
        """선택 로봇의 현재 모의 입력을 공정 상태 머신에 전달한다."""
        result = self.signals.dispatch(robot_name, self._machine(robot_name))
        if result is None:
            self._log(f"{robot_name}: 기동 조건이 아직 충족되지 않았습니다.")
        else:
            destination = self.controllers[robot_name].executed_destinations[-1]
            steps = len(result[0]["plan"])
            self._log(f"{robot_name}: DRY-RUN 공정 경로 검사 완료 ({destination}, {steps}단계)")
        return result

    def execute_all(self) -> None:
        for name in ROBOT_NAMES:
            self.execute(name)

    def emergency_stop(self, robot_name: str) -> None:
        self._machine(robot_name).emergency_stop()
        self._log(f"{robot_name}: GUI 비상정지")

    def pause(self, robot_name: str) -> None:
        """DRY-RUN 상태 머신의 일시정지 명령을 검사하고 로그에 남긴다."""
        self._machine(robot_name).pause()
        self._log(f"{robot_name}: DRY-RUN 일시정지")

    def resume(self, robot_name: str) -> None:
        """DRY-RUN 상태 머신의 재개 명령을 검사하고 로그에 남긴다."""
        self._machine(robot_name).resume()
        self._log(f"{robot_name}: DRY-RUN 운전 재개")

    def emergency_stop_all(self) -> None:
        for name in ROBOT_NAMES:
            self.emergency_stop(name)

    def reset(self, robot_name: str) -> None:
        self.signals.reset(robot_name)
        self._machine(robot_name).reset()
        self._log(f"{robot_name}: 입력 및 작업 상태 초기화")

    def reset_all(self) -> None:
        for name in ROBOT_NAMES:
            self.reset(name)

    def state(self, robot_name: str) -> str:
        return self._machine(robot_name).state.value

    def signal_snapshot(self, robot_name: str) -> ProcessSignals:
        return self.signal_provider.read(robot_name)

    def _machine(self, robot_name: str) -> RobotProcessStateMachine:
        try:
            return self.machines[robot_name]
        except KeyError as exc:
            raise ValueError(f"등록되지 않은 로봇: {robot_name}") from exc

    def _log(self, message: str) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{now}] {message}")
