"""티칭 포인트 이름을 이용해 Dobot의 공통 안전 이동 순서를 실행한다.

좌표 숫자는 이 파일에 직접 적지 않는다. 작업을 시작할 때 teaching_points.json을
읽으므로 사용자가 티칭 GUI에서 좌표를 바꾸면 다음 실행부터 자동 반영된다.
"""

from __future__ import annotations

import threading
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

from robot_control.dobot_config import load_config
from robot_control.dobot_worker import DobotWorker
from teaching.teaching_points import TeachingPointError, load_teaching_data


# 각 로봇이 사용할 수 있는 최종 배치 위치이다. 잘못된 로봇에 다른 공정의
# 포인트를 요청하는 실수를 실제 이동 명령 전에 차단한다.
ALLOWED_DESTINATIONS = {
    "Dobot_1": {"assembly_1"},
    "Dobot_2": {"assembly_1", "assembly_2"},
    "Dobot_3": {"assembly_1"},
    # Dobot_4의 실제 제품 분류명과 티칭 포인트 이름을 그대로 사용한다.
    "Dobot_4": {
        "storage_base_1st",
        "storage_base_2nd",
        "storage_base_3rd",
        "return_Lamp_a",
        "return_Lamp_b",
        "return_Seat_a",
        "return_Seat_b",
    },
}

# 그리퍼가 완전히 닫히거나 열린 뒤 다음 동작을 시작하도록 주는 안정 시간이다.
GRIPPER_SETTLE_SECONDS = 2.0
# 흡착컵이 물체를 안정적으로 붙잡을 시간을 확보한 뒤 상승한다.
SUCTION_SETTLE_SECONDS = 1.5
# 통합 GUI의 수동 검증 사이클은 기존 저속 제한을 유지한다.
MANUAL_CYCLE_MAX_PTP_SPEED_PERCENT = 10.0
# PLC START로 기동하는 자동운전은 저장 속도를 최대 30%까지 허용한다.
AUTOMATIC_CYCLE_MAX_PTP_SPEED_PERCENT = 30.0
# 기존 화면·테스트에서 사용하는 이름은 수동 사이클 제한을 뜻한다.
CYCLE_MAX_PTP_SPEED_PERCENT = MANUAL_CYCLE_MAX_PTP_SPEED_PERCENT


@dataclass(frozen=True)
class MotionStep:
    """이동 순서 한 칸을 나타내는 단순한 자료 구조."""

    kind: str
    value: str

    def describe(self) -> str:
        """모의 실행 화면에 보여줄 쉬운 설명을 반환한다."""
        if self.kind == "move":
            return f"포인트 이동: {self.value}"
        if self.kind == "wait":
            return f"엔드이펙터 안정 대기: {float(self.value):.1f}초"
        return f"엔드이펙터: {self.value}"


class MotionControllerError(RuntimeError):
    """안전 이동 순서를 시작하거나 계속할 수 없을 때 발생하는 오류."""


class RobotMotionController:
    """한 대의 Worker와 티칭 포인트를 결합해 안전 이동 순서를 관리한다."""

    def __init__(
        self,
        robot_name: str,
        worker: DobotWorker | Any,
        *,
        config: dict[str, Any] | None = None,
        teaching_data: dict[str, Any] | None = None,
    ) -> None:
        self.robot_name = robot_name
        self.worker = worker
        self.config = config if config is not None else load_config(require_all_ports=True)
        # 테스트에서는 메모리 자료를 넣을 수 있고, 실제 운전에서는 항상 최신 JSON을 읽는다.
        self.teaching_data = (
            teaching_data if teaching_data is not None else load_teaching_data()
        )
        try:
            self.robot_config = self.config["robots"][robot_name]
            self.points = self.teaching_data["robots"][robot_name]
        except KeyError as exc:
            raise MotionControllerError(f"등록되지 않은 로봇: {robot_name}") from exc

        self.tool_type = self.robot_config["end_effector"]
        self.cycle_max_ptp_speed_percent = MANUAL_CYCLE_MAX_PTP_SPEED_PERCENT
        self._pause_condition = threading.Condition()
        self._paused = False
        self._emergency = False
        self._running = False
        # None이면 아직 첫 공정 전 사용자 READY 대기가 확인되지 않은 상태이다.
        self._waiting_point: str | None = None

    def set_cycle_speed_limit(self, speed_percent: float) -> None:
        """다음 사이클에서 사용할 PTP 속도 상한을 설정한다."""
        value = float(speed_percent)
        if not 5.0 <= value <= 100.0:
            raise ValueError("사이클 PTP 속도 상한은 5~100%여야 합니다.")
        self.cycle_max_ptp_speed_percent = value

    def build_initial_plan(self) -> list[MotionStep]:
        """기계적 HOME 완료 후 이동할 사용자 READY 계획을 만든다."""
        return [MotionStep("move", "ready")]

    def _pickup_approach_route(self) -> list[str]:
        """현재 로봇 설정에 맞는 픽업 접근 경로를 바깥쪽부터 반환한다.

        대부분 로봇은 pickup_approach 한 점만 사용한다. Dobot_3처럼 장애물을
        피하기 위해 pickup_approach_1, _2를 쓰면 번호가 큰 바깥 지점부터
        _2 → _1 → pickup 순서로 들어간다.
        """
        if "pickup_approach" in self.points:
            return ["pickup_approach"]
        numbered: list[tuple[int, str]] = []
        for name in self.points:
            matched = re.fullmatch(r"pickup_approach_(\d+)", name)
            if matched:
                numbered.append((int(matched.group(1)), name))
        if not numbered:
            raise MotionControllerError(
                f"{self.robot_name}에 픽업 상승 위치가 설정되지 않았습니다."
            )
        return [name for _number, name in sorted(numbered, reverse=True)]

    def _pickup_route_for(self, destination: str) -> tuple[list[str], str]:
        """제품과 로봇에 맞는 픽업 상승 경로와 실제 픽업점을 선택한다.

        Dobot_4는 차량 하부(Main)만 base_pickup에서 집고, 램프와 시트 제품은
        jig_pickup에서 집는다. 다른 Dobot은 기존 공통 pickup 경로를 사용한다.
        """
        if self.robot_name != "Dobot_4":
            return self._pickup_approach_route(), "pickup"
        prefix = "base" if destination.startswith("storage_base") else "jig"
        return [f"{prefix}_pickup_approach"], f"{prefix}_pickup"

    def build_cycle_plan(self, destination: str) -> list[MotionStep]:
        """픽업부터 배치 후 사용자 READY 대기까지의 공통 계획을 만든다."""
        allowed = ALLOWED_DESTINATIONS.get(self.robot_name, set())
        if destination not in allowed:
            choices = ", ".join(sorted(allowed))
            raise MotionControllerError(
                f"{self.robot_name}의 배치 위치는 {choices} 중 하나여야 합니다."
            )
        # 차량 하부 적층점도 각 층 전용 상부점에서 수직 진입한다.
        approach = f"{destination}_approach"
        pickup_in, pickup_point = self._pickup_route_for(destination)
        pickup_out = list(reversed(pickup_in))
        # Dobot_3은 픽업 진입 시 1번 접근점으로 바로 이동하되,
        # 흡착 후에는 기존 1번 → 2번 접근점 순서로 빠져나온다.
        if self.robot_name == "Dobot_3":
            pickup_in = ["pickup_approach_1"]
        pick_action = "close" if self.tool_type == "gripper" else "on"
        release_action = "open" if self.tool_type == "gripper" else "off"
        # 그리퍼와 흡착컵 모두 물체를 안정적으로 잡은 뒤 상승하도록 기다린다.
        pick_settle_seconds = (
            GRIPPER_SETTLE_SECONDS
            if self.tool_type == "gripper"
            else SUCTION_SETTLE_SECONDS
        )
        after_pick = [MotionStep("wait", str(pick_settle_seconds))]
        after_release = (
            [
                MotionStep("wait", str(GRIPPER_SETTLE_SECONDS)),
                MotionStep("tool", "disable"),
            ]
            if self.tool_type == "gripper"
            else []
        )
        # Dobot_1과 Dobot_4 램프·시트 공급은 픽업 상승 후 ready를 경유한다.
        # 사이클 마지막의 ready 복귀와는 별개인 중간 경유 동작이다.
        pickup_to_destination_transfer = (
            [MotionStep("move", "ready")]
            if self.robot_name == "Dobot_1"
            or (self.robot_name == "Dobot_4" and destination in {
                "return_Lamp_a", "return_Lamp_b", "return_Seat_a", "return_Seat_b"
            })
            else []
        )
        # Dobot_2는 픽업 구역과 두 조립 구역 사이에 간섭물이 있으므로 왕복 모두
        # safe_point를 경유한다. 다른 로봇의 기존 경로에는 영향을 주지 않는다.
        safe_transfer = (
            [MotionStep("move", "safe_point")]
            if self.robot_name == "Dobot_2"
            else []
        )
        first_assembly_entry = (
            [MotionStep("move", "assembly_2_approach")]
            if self.robot_name == "Dobot_2" and destination == "assembly_1"
            else []
        )
        return_approach = (
            "assembly_1_approach2"
            if self.robot_name == "Dobot_2" and destination == "assembly_1"
            else approach
        )
        return [
            *(MotionStep("move", point) for point in pickup_in),
            MotionStep("move", pickup_point),
            MotionStep("tool", pick_action),
            *after_pick,
            *(MotionStep("move", point) for point in pickup_out),
            # Dobot_1과 Dobot_4 램프·시트 공급: 픽업 상승 위치 → ready → 목적지 상부
            *pickup_to_destination_transfer,
            # 픽업 상승 위치 → safe_point → 선택 조립 상승 위치
            *safe_transfer,
            # Dobot_2 첫 번째 조립은 2번 조립 상부를 거쳐 1번 조립 상부로 진입한다.
            *first_assembly_entry,
            MotionStep("move", approach),
            MotionStep("move", destination),
            MotionStep("tool", release_action),
            *after_release,
            MotionStep("move", return_approach),
            # 조립 상승 위치에서 빠져나올 때도 같은 안전점을 반대로 경유한다.
            *safe_transfer,
            # PLC의 다음 START를 사용자가 지정한 안전 대기점에서 기다린다.
            MotionStep("move", "ready"),
        ]

    def build_base_supply_plan(self, storage_point: str) -> list[MotionStep]:
        """Dobot_4 적층 창고의 최상단 차량 하부를 메인 컨베이어로 공급한다."""
        if self.robot_name != "Dobot_4":
            raise MotionControllerError("베이스 공급 사이클은 Dobot_4만 실행할 수 있습니다.")
        if storage_point not in {
            "storage_base_1st", "storage_base_2nd", "storage_base_3rd"
        }:
            raise MotionControllerError(f"잘못된 차량 하부 적층 위치: {storage_point}")
        pick_action = "close" if self.tool_type == "gripper" else "on"
        release_action = "open" if self.tool_type == "gripper" else "off"
        settle_seconds = (
            GRIPPER_SETTLE_SECONDS
            if self.tool_type == "gripper"
            else SUCTION_SETTLE_SECONDS
        )
        after_release = (
            [
                MotionStep("wait", str(GRIPPER_SETTLE_SECONDS)),
                MotionStep("tool", "disable"),
            ]
            if self.tool_type == "gripper"
            else []
        )
        storage_approach = f"{storage_point}_approach"
        return [
            MotionStep("move", storage_approach),
            MotionStep("move", storage_point),
            MotionStep("tool", pick_action),
            MotionStep("wait", str(settle_seconds)),
            MotionStep("move", storage_approach),
            MotionStep("move", "main_conveyor_approach"),
            MotionStep("move", "main_conveyor"),
            MotionStep("tool", release_action),
            *after_release,
            MotionStep("move", "main_conveyor_approach"),
            MotionStep("move", "ready"),
        ]

    def _validate_plan(self, steps: Iterable[MotionStep]) -> list[MotionStep]:
        """모든 이동 포인트가 등록됐는지 한 번에 검사한다."""
        checked = list(steps)
        missing: list[str] = []
        for step in checked:
            if step.kind != "move":
                continue
            point = self.points.get(step.value)
            if not point or not point.get("valid") or point.get("pose") is None:
                missing.append(step.value)
        if missing:
            names = ", ".join(dict.fromkeys(missing))
            raise TeachingPointError(
                f"{self.robot_name}의 미등록 필수 포인트로 운전을 차단했습니다: {names}"
            )
        return checked

    def preview(self, steps: Iterable[MotionStep]) -> list[str]:
        """로봇을 움직이지 않고 검사된 실행 순서만 반환한다."""
        return [step.describe() for step in self._validate_plan(steps)]

    def initialize(self, mechanical_home: bool = True) -> list[dict[str, Any]]:
        """처음 연결할 때 기계적 HOME과 사용자 READY 이동을 차례로 실행한다."""
        steps = self._validate_plan(self.build_initial_plan())
        results: list[dict[str, Any]] = []
        if mechanical_home:
            results.append(self.worker.home())
        results.extend(self.execute(steps))
        # 기계적 HOME 여부와 관계없이 사용자 지정 ready 포인트 도착이 확인된 상태다.
        self._waiting_point = "ready"
        return results

    def run_cycle(self, destination: str) -> list[dict[str, Any]]:
        """READY 대기가 확인된 뒤 한 사이클을 실행하고 다시 READY에서 기다린다."""
        if self._waiting_point is None:
            raise MotionControllerError(
                "첫 공정 전 HOME 초기화 또는 작업자 READY 승인을 완료하세요."
            )
        results = self.execute(self.build_cycle_plan(destination))
        self._waiting_point = "ready"
        return results

    def run_base_supply_cycle(self, storage_point: str) -> list[dict[str, Any]]:
        """READY에서 차량 하부를 꺼내 메인 컨베이어에 놓고 READY로 복귀한다."""
        if self._waiting_point is None:
            raise MotionControllerError(
                "첫 공정 전 HOME 초기화 또는 작업자 READY 승인을 완료하세요."
            )
        results = self.execute(self.build_base_supply_plan(storage_point))
        self._waiting_point = "ready"
        return results

    @property
    def waiting_point(self) -> str | None:
        """현재 확인된 대기 위치를 GUI가 표시할 수 있도록 반환한다."""
        return self._waiting_point

    def accept_operator_ready(self) -> None:
        """작업자가 현재 위치를 확인하고 HOME 없이 공정 시작을 승인한다."""
        self._waiting_point = "ready"

    def confirm_ready_pose(self, current_pose: dict[str, float], tolerance: float = 0.5) -> None:
        """현재 좌표가 사용자 READY와 일치할 때만 대기 상태를 인계한다."""
        self.confirm_waiting_pose("ready", current_pose, tolerance)

    def confirm_waiting_pose(
        self, point_name: str, current_pose: dict[str, float], tolerance: float = 0.5
    ) -> None:
        """현재 좌표가 지정 대기 포인트와 일치할 때만 공정 재개를 허용한다."""
        self._validate_plan([MotionStep("move", point_name)])
        target = self.points[point_name]["pose"]
        errors = {axis: abs(float(current_pose[axis]) - float(target[axis]))
                  for axis in "xyzr"}
        if any(error > tolerance for error in errors.values()):
            raise MotionControllerError(
                f"현재 좌표가 {point_name}과 다릅니다: 축별 오차={errors}, 허용={tolerance}"
            )
        self._waiting_point = point_name

    def execute(self, steps: Iterable[MotionStep]) -> list[dict[str, Any]]:
        """검사된 순서를 실행하며 단계 사이에서 일시정지와 비상정지를 확인한다."""
        checked = self._validate_plan(steps)
        if self._running:
            raise MotionControllerError("이미 이동 순서를 실행 중입니다.")
        self._running = True
        results: list[dict[str, Any]] = []
        try:
            for step in checked:
                self._wait_until_runnable()
                if step.kind == "move":
                    point = self.points[step.value]
                    # 저장 속도와 현재 운전 모드의 상한 중 낮은 값을 실제로 요청한다.
                    speed = min(
                        float(point["speed_percent"]),
                        self.cycle_max_ptp_speed_percent,
                    )
                    result = self.worker.move_ptp(
                        dict(point["pose"]),
                        speed_percent=speed,
                        arrival_timeout=90.0,
                        tolerance=0.25,
                        allow_large_move=True,
                    )
                elif step.kind == "tool":
                    result = self.worker.set_end_effector(self.tool_type, step.value)
                elif step.kind == "wait":
                    seconds = float(step.value)
                    self._wait_safely(seconds)
                    result = {"waited_seconds": seconds}
                else:
                    raise MotionControllerError(f"알 수 없는 이동 단계: {step.kind}")
                results.append(result)
        except Exception:
            self.safe_stop()
            raise
        finally:
            self._running = False
        return results

    def _wait_safely(self, seconds: float) -> None:
        """그리퍼 안정 대기 중에도 일시정지와 비상정지를 계속 확인한다."""
        remaining = seconds
        while remaining > 0.0:
            self._wait_until_runnable()
            # 한 번에 오래 잠들지 않아 비상정지를 최대 약 50ms마다 확인한다.
            interval = min(0.05, remaining)
            started = time.monotonic()
            time.sleep(interval)
            # 일시정지로 기다린 시간은 제외하고 실제 안정 대기 시간만 차감한다.
            remaining -= time.monotonic() - started

    def pause(self) -> None:
        """현재 이동이 끝난 뒤 다음 단계로 넘어가지 않게 한다."""
        with self._pause_condition:
            self._paused = True

    def resume(self) -> None:
        """일시정지를 해제하고 다음 단계를 실행한다."""
        with self._pause_condition:
            self._paused = False
            self._pause_condition.notify_all()

    def emergency_stop(self) -> None:
        """다음 단계 실행을 금지하고 현재 이동의 강제 정지를 요청한다."""
        with self._pause_condition:
            self._emergency = True
            self._paused = False
            self._pause_condition.notify_all()
        self.safe_stop()

    def reset(self) -> None:
        """오류 원인을 제거한 뒤 새 작업을 받을 수 있도록 내부 상태를 초기화한다."""
        if self._running:
            raise MotionControllerError("이동 순서 실행 중에는 초기화할 수 없습니다.")
        with self._pause_condition:
            self._emergency = False
            self._paused = False
            # 초기화 후에는 HOME 이동을 다시 확인해야 첫 공정을 시작할 수 있다.
            self._waiting_point = None

    def safe_stop(self) -> None:
        """가능한 정지 명령을 모두 보내되 한 명령 실패가 다음 정지를 막지 않게 한다."""
        # force_stop은 일반 명령 Queue와 별도 경로라서 PTP/HOME 실행 중에도 동작한다.
        # 이전 형식의 가짜 Worker나 호환 Worker에는 이 메서드가 없을 수 있다.
        force_stop = getattr(self.worker, "force_stop", None)
        if force_stop is not None:
            try:
                force_stop()
                return
            except Exception:
                pass
        # 강제 정지 경로가 실패한 경우 기존 정지 명령도 끝까지 시도한다.
        for stop_command in (self.worker.stop_jog, self.worker.stop_queue):
            try:
                stop_command()
            except Exception:
                pass

    def _wait_until_runnable(self) -> None:
        """일시정지 중에는 기다리고 비상정지 상태면 즉시 실행을 중단한다."""
        with self._pause_condition:
            while self._paused and not self._emergency:
                self._pause_condition.wait(timeout=0.1)
            if self._emergency:
                raise MotionControllerError("비상정지 상태이므로 이동을 중단했습니다.")
