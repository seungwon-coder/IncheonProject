"""Dobot 한 대를 독립 프로세스에서 담당하는 Worker.

내부 sdk 폴더의 Dobot SDK는 현재 장치 번호를 전역 변수에 저장한다. 한 프로세스가
4대를 직접 관리하면 장치 번호가 서로 덮어써질 수 있으므로, 각 Dobot마다 별도의
Python 프로세스를 실행하여 SDK 상태가 섞이지 않게 한다.

읽기 명령 외에는 안전 범위가 제한된 저속 조그와 즉시 정지만 허용한다.
"""

from __future__ import annotations

import multiprocessing as mp
import math
import os
import queue
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from robot_control.dobot_protocol import (
    CommandMessage,
    CommandName,
    ResponseMessage,
    error_response,
    success_response,
)
from robot_control.dobot_jog import JogRequest
from robot_control.dobot_ptp import PTPRequest, PTP_RESPONSE_MARGIN_SECONDS
from robot_control.dobot_end_effector import EndEffectorRequest
from robot_control.dobot_alarm import alarm_details
from robot_control.dobot_errors import DobotCommandError, DobotCommandTimeout, validate_timeout
from robot_control.robot_position_cache import PositionCaptureSdk, read_position


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SDK_DIR = (BASE_DIR.parent / "sdk").resolve()
# 강제 정지 신호를 확인하는 최대 간격이다. 이동 중에는 약 20ms마다 확인한다.
FORCE_STOP_POLL_SECONDS = 0.02
HOME_PARAMETER_ROBOTS = {"Dobot_1", "Dobot_2"}
# 기계적 HOME은 로봇 자세와 속도에 따라 30초를 넘길 수 있어 60초까지 기다린다.
# 바깥 프로세스는 내부 완료 확인 결과가 전달될 5초의 여유를 추가로 가진다.
HOME_MOTION_TIMEOUT_SECONDS = 60.0
HOME_RESPONSE_MARGIN_SECONDS = 5.0


class WorkerRecoveryError(RuntimeError):
    """정해진 재시도 횟수 안에 Worker 복구가 끝나지 않았을 때 발생하는 오류."""


def _load_sdk(sdk_dir: str) -> Any:
    """자식 프로세스 안에서 지정한 폴더의 Dobot SDK를 불러온다."""
    path = Path(sdk_dir)
    if not (path / "DobotDllType.py").is_file() or not (path / "DobotDll.dll").is_file():
        raise FileNotFoundError(f"Dobot SDK 파일이 없습니다: {path}")
    sys.path.insert(0, str(path))
    import DobotDllType as d_type  # type: ignore

    return d_type


def _alarm_codes(d_type: Any, api: Any) -> list[int]:
    """SDK가 비트 묶음으로 주는 알람을 사람이 읽기 쉬운 번호 목록으로 바꾼다."""
    result = d_type.GetAlarmsState(api)
    if not result or len(result) < 2:
        raise RuntimeError(f"알람 응답이 올바르지 않습니다: {result}")
    raw, length = result[0], int(result[1])
    codes: list[int] = []
    # 한 바이트에는 8개의 알람 ON/OFF 상태가 들어 있다.
    for byte_index in range(length):
        value = raw[byte_index]
        if isinstance(value, str):
            value = ord(value)
        elif isinstance(value, bytes):
            value = value[0]
        else:
            value = int(value)
        for bit_index in range(8):
            if value & (1 << bit_index):
                codes.append(byte_index * 8 + bit_index)
    return codes


def _configure_ptp_motion(d_type: Any, api: Any, speed_percent: float) -> int:
    """비운 큐에 속도 설정을 등록한다. 뒤의 이동과 순서대로 실행된다."""
    reply = d_type.SetPTPCommonParams(api, speed_percent, 10, 1)
    if not isinstance(reply, (list, tuple)) or not reply:
        raise RuntimeError(f"PTP 속도 설정 큐 등록 응답 오류: {reply}")
    index = reply[0]
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise RuntimeError(f"PTP 속도 설정 큐 번호 오류: {reply}")
    return index


def _execute_ptp_segment(
    d_type: Any,
    api: Any,
    target: dict[str, float],
    tolerance: float,
    deadline: float,
    stop_requested: Any,
    stop_applied: Any,
    speed_queue_index: int | None = None,
) -> tuple[dict[str, float], int, float]:
    """PTP 한 구간을 실행하고 도착 좌표·큐 번호·실제 이동시간을 반환한다."""
    segment_before = _pose(d_type, api)
    queued_reply = d_type.SetPTPCmd(
        api,
        d_type.PTPMode.PTPMOVJXYZMode,
        target["x"], target["y"], target["z"], target["r"],
        1,
    )
    if queued_reply is None or len(queued_reply) < 1:
        raise RuntimeError(f"PTP 큐 등록 응답 오류: {queued_reply}")
    target_index = int(queued_reply[0])
    if speed_queue_index is not None and target_index <= speed_queue_index:
        d_type.SetQueuedCmdClear(api)
        raise RuntimeError("PTP 속도 설정과 이동 명령의 큐 순서가 올바르지 않습니다.")
    d_type.SetQueuedCmdStartExec(api)
    started_at = time.monotonic()
    motion_start_deadline = started_at + 3.0
    motion_observed = False
    stable_reads = 0
    last_pose = segment_before
    try:
        while time.monotonic() < deadline:
            if _apply_force_stop(d_type, api, stop_requested, stop_applied):
                raise RuntimeError("강제 정지 요청으로 PTP 이동을 중단했습니다.")
            last_pose = _pose(d_type, api)
            new_alarms = _alarm_codes(d_type, api)
            if new_alarms:
                d_type.SetQueuedCmdForceStopExec(api)
                raise RuntimeError(f"PTP 시작 후 알람 발생: {new_alarms}")
            index_reply = d_type.GetQueuedCmdCurrentIndex(api)
            current_index = int(index_reply[0])
            arrived = all(
                abs(last_pose[axis] - value) <= tolerance
                for axis, value in target.items()
            )
            command_done = current_index >= target_index
            motion_observed = motion_observed or any(
                abs(last_pose[axis] - segment_before[axis]) > 0.05
                for axis in "xyzr"
            )
            stable_reads = stable_reads + 1 if arrived and command_done else 0
            if stable_reads >= 3:
                return last_pose, target_index, time.monotonic() - started_at
            if (
                not arrived
                and not motion_observed
                and time.monotonic() >= motion_start_deadline
            ):
                d_type.SetQueuedCmdForceStopExec(api)
                raise RuntimeError(
                    "PTP 명령 후 3초 동안 좌표 변화가 없어 이동을 중단했습니다. "
                    "알람, 큐 실행 상태, 목표 좌표를 확인하세요."
                )
            time.sleep(FORCE_STOP_POLL_SECONDS)
        d_type.SetQueuedCmdForceStopExec(api)
        raise TimeoutError(f"PTP 도착 확인 시간 초과; 목표={target}, 현재={last_pose}")
    finally:
        d_type.SetQueuedCmdStopExec(api)
        d_type.SetQueuedCmdClear(api)


def _pose(d_type: Any, api: Any) -> dict[str, float]:
    """현재 X/Y/Z/R 좌표만 읽어 같은 형식으로 반환한다."""
    pose = d_type.GetPose(api)
    if pose is None or len(pose) < 8:
        raise RuntimeError(f"좌표 응답이 올바르지 않습니다: {pose}")
    return {
        "x": float(pose[0]), "y": float(pose[1]),
        "z": float(pose[2]), "r": float(pose[3]),
    }


def _home_pose_from_payload(payload: dict[str, Any]) -> dict[str, float]:
    """HOME 복귀 위치로 저장할 X/Y/Z/R 값의 형식을 안전하게 검사한다."""
    pose: dict[str, float] = {}
    for axis in "xyzr":
        value = payload.get(axis)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"HOME {axis.upper()} 좌표는 숫자여야 합니다.")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"HOME {axis.upper()} 좌표는 유한한 숫자여야 합니다.")
        pose[axis] = number
    return pose


def _apply_force_stop(
    d_type: Any,
    api: Any,
    stop_requested: Any,
    stop_applied: Any,
) -> bool:
    """별도 신호가 켜졌다면 JOG와 큐 이동을 즉시 정지한다.

    일반 명령 Queue와 분리된 Event를 사용하므로, PTP/HOME 명령의 완료를 기다리는
    중에도 정지 요청을 확인할 수 있다. 반환값 True는 정지를 실제로 시도했다는 뜻이다.
    """
    if not stop_requested.is_set():
        return False
    stop_requested.clear()
    try:
        d_type.SetJOGCmd(api, 0, d_type.JC.JogIdle, 0)
        d_type.SetQueuedCmdForceStopExec(api)
        d_type.SetQueuedCmdClear(api)
    finally:
        # 호출한 쪽이 정지 명령 처리 여부를 기다릴 수 있도록 완료 신호를 켠다.
        stop_applied.set()
    return True


def _worker_main(
    robot_name: str,
    port: str,
    baudrate: int,
    sdk_dir: str,
    commands: mp.Queue,
    responses: mp.Queue,
    stop_requested: Any,
    stop_applied: Any,
    position_cache: Any,
) -> None:
    """자식 프로세스에서 실행되는 실제 통신 반복문.

    commands 큐로 명령을 받고 responses 큐로 결과를 돌려준다. 큐는 서로 다른
    프로세스 사이에서 데이터를 전달하는 우편함과 비슷하다.
    """
    api = None
    d_type = None
    connected = False
    # JOG_START 후 정지 명령이 오지 않아도 제한 시간에 멈추기 위한 상태값이다.
    jog_active = False
    jog_deadline = 0.0
    try:
        # 이 Worker만의 SDK와 DLL을 준비하고 담당 포트 한 개에 연결한다.
        # SDK 함수는 그대로 사용하되 GetPose 결과를 SCADA용 공유 메모리에도 복사한다.
        # 추가 COM 연결은 만들지 않으므로 한 로봇의 SDK 소유자는 계속 이 Worker 하나다.
        d_type = PositionCaptureSdk(_load_sdk(sdk_dir), position_cache)
        api = d_type.load()
        reply = d_type.ConnectDobot(api, port, baudrate)
        code = int(reply[0])
        if code != d_type.DobotConnect.DobotConnect_NoError:
            responses.put({"event": "startup", "ok": False, "error": f"연결 상태 코드 {code}"})
            return
        master_type, slave_type = int(reply[1]), int(reply[2])
        effective_type = master_type if master_type != 1 else slave_type
        if effective_type not in (d_type.DevType.Magician, d_type.DevType.MagicianLite):
            responses.put({"event": "startup", "ok": False, "error": f"Dobot 유형 아님: {effective_type}"})
            return
        connected = True
        responses.put(
            {
                "event": "startup",
                "ok": True,
                "robot": robot_name,
                "port": port,
                # PID(Process ID)는 Windows가 각 실행 프로세스에 부여하는 번호다.
                # 네 Worker의 PID가 다르면 실제로 프로세스가 분리됐음을 확인할 수 있다.
                "process_id": os.getpid(),
                "firmware_name": str(reply[3]),
                "firmware_version": str(reply[4]),
            }
        )

        # shutdown 명령을 받을 때까지 메인 프로세스의 요청을 하나씩 처리한다.
        while True:
            if _apply_force_stop(d_type, api, stop_requested, stop_applied):
                jog_active = False
                jog_deadline = 0.0
            try:
                # 조그 중에는 남은 안전 시간까지만 명령을 기다린다. 시간이 끝나면
                # Queue가 비어 있어도 아래 except에서 로봇을 자동 정지한다.
                # 정지 Event도 빠르게 확인해야 하므로 평상시에도 짧게 나누어 기다린다.
                wait_timeout = FORCE_STOP_POLL_SECONDS
                if jog_active:
                    wait_timeout = min(
                        wait_timeout, max(0.0, jog_deadline - time.monotonic())
                    )
                raw_request = commands.get(timeout=wait_timeout)
            except queue.Empty:
                if jog_active and time.monotonic() >= jog_deadline:
                    d_type.SetJOGCmd(api, 0, d_type.JC.JogIdle, 0)
                    jog_active = False
                    jog_deadline = 0.0
                # 명령이 없는 대기 중에도 SCADA 좌표가 오래되지 않도록 1초 간격으로
                # 읽는다. sample_idle 내부에서 실제 읽기 주기를 제한한다.
                d_type.sample_idle(api)
                continue
            try:
                request = CommandMessage.from_dict(raw_request)
                request_id = request.request_id
                command = request.command
                if command is CommandName.SHUTDOWN:
                    if jog_active:
                        d_type.SetJOGCmd(api, 0, d_type.JC.JogIdle, 0)
                        jog_active = False
                    responses.put(success_response(request_id, "shutdown"))
                    return
                if command is CommandName.PING:
                    result: Any = {
                        "robot": robot_name,
                        "port": port,
                        "process_id": os.getpid(),
                        "time": time.time(),
                    }
                elif command is CommandName.GET_IDENTITY:
                    # 시리얼번호와 장치명은 USB 포트가 바뀌어도 같은 로봇을
                    # 다시 찾기 위한 후보 식별값이다. 두 명령 모두 읽기 전용이다.
                    serial_number = str(d_type.GetDeviceSN(api)[0]).strip()
                    device_name = str(d_type.GetDeviceName(api)[0]).strip()
                    result = {
                        "robot": robot_name,
                        "port": port,
                        "serial_number": serial_number,
                        "device_name": device_name,
                    }
                elif command is CommandName.GET_STATUS:
                    # GetPose는 이동 명령이 아니라 현재 좌표를 읽는 명령이다.
                    raw_pose = d_type.GetPose(api)
                    if raw_pose is None or len(raw_pose) < 8:
                        raise RuntimeError(f"좌표 응답이 올바르지 않습니다: {raw_pose}")
                    result = {
                        "robot": robot_name,
                        "port": port,
                        "process_id": os.getpid(),
                        "pose": {
                            "x": float(raw_pose[0]), "y": float(raw_pose[1]),
                            "z": float(raw_pose[2]), "r": float(raw_pose[3]),
                        },
                        "joints": [float(value) for value in raw_pose[4:8]],
                        "alarms": _alarm_codes(d_type, api),
                        "read_at": time.time(),
                    }
                elif command is CommandName.JOG_STOP:
                    # JogIdle(0)은 현재 좌표 조그를 즉시 멈추는 SDK 명령이다.
                    d_type.SetJOGCmd(api, 0, d_type.JC.JogIdle, 0)
                    jog_active = False
                    jog_deadline = 0.0
                    result = {
                        "robot": robot_name,
                        "stopped": True,
                        "pose": _pose(d_type, api),
                    }
                elif command is CommandName.JOG_START:
                    if jog_active:
                        raise RuntimeError("이미 조그 운전 중입니다. 먼저 정지하세요.")
                    jog = JogRequest.from_payload(request.payload)
                    alarms = _alarm_codes(d_type, api)
                    if alarms:
                        raise RuntimeError(f"알람이 있어 조그를 차단했습니다: {alarms}")
                    before = _pose(d_type, api)
                    if jog.is_joint:
                        # J1~J4의 최대속도와 가속도를 설정한 뒤 1~100% 비율을 적용한다.
                        d_type.SetJOGJointParams(
                            api, 20, 20, 20, 20, 20, 20, 20, 20, 0
                        )
                    else:
                        d_type.SetJOGCoordinateParams(
                            api, 50, 50, 50, 50, 40, 40, 30, 30, 0
                        )
                    d_type.SetJOGCommonParams(api, jog.speed_percent, 10, 0)
                    d_type.SetJOGCmd(api, int(jog.is_joint), jog.command_code, 0)
                    jog_active = True
                    jog_deadline = time.monotonic() + jog.duration
                    result = {
                        "robot": robot_name,
                        "started": True,
                        "axis": jog.axis,
                        "direction": jog.direction,
                        "watchdog_seconds": jog.duration,
                        "speed_percent": jog.speed_percent,
                        "before": before,
                    }
                elif command is CommandName.JOG_KEEPALIVE:
                    # GUI 버튼을 계속 누르고 있을 때만 감시 시간을 연장한다.
                    # JOG가 이미 끝났다면 새 이동을 시작하지 않고 오류로 알린다.
                    if not jog_active:
                        raise RuntimeError("연장할 JOG 운전이 없습니다.")
                    watchdog = float(request.payload.get("watchdog_seconds", 0.25))
                    if not 0.10 <= watchdog <= 0.25:
                        raise ValueError("JOG 감시 시간은 0.10~0.25초여야 합니다.")
                    jog_deadline = time.monotonic() + watchdog
                    result = {
                        "robot": robot_name,
                        "kept_alive": True,
                        "watchdog_seconds": watchdog,
                    }
                elif command is CommandName.JOG_FOR:
                    jog = JogRequest.from_payload(request.payload)
                    alarms = _alarm_codes(d_type, api)
                    if alarms:
                        raise RuntimeError(f"알람이 있어 조그를 차단했습니다: {alarms}")
                    before = _pose(d_type, api)
                    # 좌표별 최대속도를 낮게 잡고, 다시 1~10% 비율만 허용한다.
                    if jog.is_joint:
                        d_type.SetJOGJointParams(
                            api, 20, 20, 20, 20, 20, 20, 20, 20, 0
                        )
                    else:
                        d_type.SetJOGCoordinateParams(
                            api, 50, 50, 50, 50, 40, 40, 30, 30, 0
                        )
                    d_type.SetJOGCommonParams(api, jog.speed_percent, 10, 0)
                    try:
                        d_type.SetJOGCmd(api, int(jog.is_joint), jog.command_code, 0)
                        time.sleep(jog.duration)
                    finally:
                        # 중간 코드에서 오류가 나도 정지 명령을 보내도록 finally를 사용한다.
                        d_type.SetJOGCmd(api, 0, d_type.JC.JogIdle, 0)
                    result = {
                        "robot": robot_name,
                        "axis": jog.axis,
                        "direction": jog.direction,
                        "duration": jog.duration,
                        "speed_percent": jog.speed_percent,
                        "before": before,
                        "after": _pose(d_type, api),
                    }
                elif command is CommandName.MOVE_PTP:
                    ptp = PTPRequest.from_payload(request.payload)
                    alarms = _alarm_codes(d_type, api)
                    if alarms:
                        raise RuntimeError(f"알람이 있어 PTP 이동을 차단했습니다: {alarms}")
                    before = _pose(d_type, api)
                    ptp.validate_small_move(before)
                    # PTP 명령은 전용 큐를 초기화한 뒤 등록하고 실행한다. 이전에 큐가
                    # 정지된 상태였더라도 항상 같은 순서로 확실하게 시작할 수 있다.
                    d_type.SetQueuedCmdStopExec(api)
                    d_type.SetQueuedCmdClear(api)
                    speed_queue_index = _configure_ptp_motion(
                        d_type, api, ptp.speed_percent
                    )
                    deadline = time.monotonic() + ptp.timeout
                    last_pose, target_index, _motion_seconds = _execute_ptp_segment(
                        d_type,
                        api,
                        ptp.target(),
                        ptp.tolerance,
                        deadline,
                        stop_requested,
                        stop_applied,
                        speed_queue_index=speed_queue_index,
                    )
                    result = {
                        "robot": robot_name,
                        "before": before,
                        "target": ptp.target(),
                        "after": last_pose,
                        "arrived": True,
                        "tolerance": ptp.tolerance,
                        "speed_percent_requested": ptp.speed_percent,
                        "speed_queue_index": speed_queue_index,
                        "queue_index": target_index,
                    }
                elif command is CommandName.QUEUE_CLEAR:
                    if jog_active:
                        raise RuntimeError("조그 운전 중에는 큐를 초기화할 수 없습니다.")
                    # 컨트롤러에 대기 중인 명령을 모두 삭제한다. 현재 좌표는 바뀌지 않는다.
                    reply = d_type.SetQueuedCmdClear(api)
                    result = {
                        "robot": robot_name,
                        "cleared": True,
                        "sdk_reply": list(reply) if reply is not None else [],
                    }
                elif command is CommandName.QUEUE_START:
                    if jog_active:
                        raise RuntimeError("조그 운전 중에는 큐를 시작할 수 없습니다.")
                    # 미리 쌓아 둔 큐 명령의 실행을 시작한다.
                    reply = d_type.SetQueuedCmdStartExec(api)
                    result = {
                        "robot": robot_name,
                        "started": True,
                        "sdk_reply": list(reply) if reply is not None else [],
                    }
                elif command is CommandName.QUEUE_STOP:
                    # 일반 정지는 현재 명령을 마친 뒤 다음 큐 명령 실행을 멈춘다.
                    reply = d_type.SetQueuedCmdStopExec(api)
                    result = {
                        "robot": robot_name,
                        "stopped": True,
                        "sdk_reply": list(reply) if reply is not None else [],
                    }
                elif command is CommandName.GET_QUEUE_INDEX:
                    reply = d_type.GetQueuedCmdCurrentIndex(api)
                    if reply is None or len(reply) < 1:
                        raise RuntimeError(f"큐 인덱스 응답이 올바르지 않습니다: {reply}")
                    result = {
                        "robot": robot_name,
                        "current_index": int(reply[0]),
                    }
                elif command is CommandName.SET_END_EFFECTOR:
                    tool = EndEffectorRequest.from_payload(request.payload)
                    enable, on = tool.sdk_values
                    if tool.tool_type == "gripper":
                        reply = d_type.SetEndEffectorGripper(api, enable, on, 0)
                    else:
                        reply = d_type.SetEndEffectorSuctionCup(api, enable, on, 0)
                    result = {
                        "robot": robot_name,
                        "tool_type": tool.tool_type,
                        "action": tool.action,
                        "enabled": enable,
                        "on": on,
                        "sdk_reply": list(reply) if reply is not None else [],
                    }
                elif command is CommandName.GET_END_EFFECTOR:
                    tool_type = str(request.payload.get("tool_type", "")).lower()
                    # 상태 읽기에도 같은 검사를 적용하기 위해 안전한 동작명을 함께 넣는다.
                    check_action = "open" if tool_type == "gripper" else "off"
                    tool = EndEffectorRequest.from_payload(
                        {"tool_type": tool_type, "action": check_action}
                    )
                    if tool.tool_type == "gripper":
                        reply = d_type.GetEndEffectorGripper(api)
                    else:
                        reply = d_type.GetEndEffectorSuctionCup(api)
                    if reply is None or len(reply) < 1:
                        raise RuntimeError(f"엔드이펙터 상태 응답 오류: {reply}")
                    result = {
                        "robot": robot_name,
                        "tool_type": tool.tool_type,
                        "on": bool(reply[0]),
                    }
                elif command is CommandName.GET_ALARMS:
                    codes = _alarm_codes(d_type, api)
                    result = {
                        "robot": robot_name,
                        "codes": codes,
                        "details": alarm_details(codes),
                        "has_alarm": bool(codes),
                    }
                elif command is CommandName.GET_HOME_PARAMS:
                    # 조회는 로봇을 움직이지 않고 컨트롤러의 HOME 복귀 좌표만 읽는다.
                    reply = d_type.GetHOMEParams(api)
                    if reply is None or len(reply) < 4:
                        raise RuntimeError(f"HOME 파라미터 응답 오류: {reply}")
                    result = {
                        "robot": robot_name,
                        "pose": {
                            "x": float(reply[0]), "y": float(reply[1]),
                            "z": float(reply[2]), "r": float(reply[3]),
                        },
                    }
                elif command is CommandName.SET_HOME_PARAMS:
                    if robot_name not in HOME_PARAMETER_ROBOTS:
                        raise RuntimeError("HOME 복귀 위치 변경은 Dobot_1과 Dobot_2만 허용됩니다.")
                    pose = _home_pose_from_payload(request.payload)
                    # 값만 변경하며 이 명령 자체로는 HOME 이동을 실행하지 않는다.
                    d_type.SetJOGCmd(api, 0, d_type.JC.JogIdle, 0)
                    jog_active = False
                    jog_deadline = 0.0
                    d_type.SetQueuedCmdStopExec(api)
                    d_type.SetQueuedCmdClear(api)
                    # 일부 Magician 펌웨어는 즉시 명령(isQueued=0)을 정상 응답해도
                    # 값을 반영하지 않는다. 큐에 등록하고 해당 인덱스의 실행 완료를
                    # 확인해야 저장 여부를 확실하게 판정할 수 있다.
                    queued_reply = d_type.SetHOMEParams(
                        api, pose["x"], pose["y"], pose["z"], pose["r"], isQueued=1
                    )
                    if queued_reply is None or len(queued_reply) < 1:
                        raise RuntimeError(f"HOME 파라미터 큐 등록 응답 오류: {queued_reply}")
                    target_index = int(queued_reply[0])
                    d_type.SetQueuedCmdStartExec(api)
                    deadline = time.monotonic() + 3.0
                    try:
                        while time.monotonic() < deadline:
                            index_reply = d_type.GetQueuedCmdCurrentIndex(api)
                            if index_reply and int(index_reply[0]) >= target_index:
                                break
                            time.sleep(FORCE_STOP_POLL_SECONDS)
                        else:
                            raise TimeoutError("HOME 파라미터 저장이 3초 안에 끝나지 않았습니다.")
                    finally:
                        d_type.SetQueuedCmdStopExec(api)
                        d_type.SetQueuedCmdClear(api)
                    verified = d_type.GetHOMEParams(api)
                    if verified is None or len(verified) < 4:
                        raise RuntimeError(f"변경 후 HOME 파라미터 확인 오류: {verified}")
                    saved = {
                        "x": float(verified[0]), "y": float(verified[1]),
                        "z": float(verified[2]), "r": float(verified[3]),
                    }
                    if any(abs(saved[axis] - pose[axis]) > 0.01 for axis in "xyzr"):
                        raise RuntimeError(f"HOME 파라미터 저장값 불일치: 요청={pose}, 확인={saved}")
                    result = {"robot": robot_name, "pose": saved, "saved": True}
                elif command is CommandName.CLEAR_ALARMS:
                    before_codes = _alarm_codes(d_type, api)
                    # 초기화 전에 조그와 큐 실행을 멈춰 예기치 않은 재동작을 방지한다.
                    d_type.SetJOGCmd(api, 0, d_type.JC.JogIdle, 0)
                    jog_active = False
                    jog_deadline = 0.0
                    d_type.SetQueuedCmdStopExec(api)
                    d_type.ClearAllAlarmsState(api)
                    # 컨트롤러가 초기화 결과를 갱신할 짧은 시간을 준다.
                    time.sleep(0.2)
                    after_codes = _alarm_codes(d_type, api)
                    result = {
                        "robot": robot_name,
                        "before": alarm_details(before_codes),
                        "after": alarm_details(after_codes),
                        "cleared": not after_codes,
                    }
                elif command is CommandName.HOME:
                    alarms = _alarm_codes(d_type, api)
                    if alarms:
                        raise RuntimeError(f"알람이 있어 HOME 이동을 차단했습니다: {alarms}")
                    # HOME은 컨트롤러에 저장된 기계적 원점 절차를 실행한다.
                    d_type.SetJOGCmd(api, 0, d_type.JC.JogIdle, 0)
                    jog_active = False
                    jog_deadline = 0.0
                    d_type.SetQueuedCmdStopExec(api)
                    d_type.SetQueuedCmdClear(api)
                    home_reply = d_type.SetHOMECmd(api, temp=0, isQueued=1)
                    if home_reply is None or len(home_reply) < 1:
                        raise RuntimeError(f"HOME 큐 등록 응답 오류: {home_reply}")
                    target_index = int(home_reply[0])
                    d_type.SetQueuedCmdStartExec(api)
                    deadline = time.monotonic() + HOME_MOTION_TIMEOUT_SECONDS
                    try:
                        while time.monotonic() < deadline:
                            if _apply_force_stop(
                                d_type, api, stop_requested, stop_applied
                            ):
                                raise RuntimeError("강제 정지 요청으로 HOME 이동을 중단했습니다.")
                            # HOME 이동 중에도 약 1초 간격으로 현재 좌표를 SCADA에
                            # 제공한다. 이 호출은 이동 명령을 만들지 않는다.
                            d_type.sample_idle(api)
                            current_reply = d_type.GetQueuedCmdCurrentIndex(api)
                            if current_reply and int(current_reply[0]) >= target_index:
                                break
                            time.sleep(FORCE_STOP_POLL_SECONDS)
                        else:
                            d_type.SetQueuedCmdForceStopExec(api)
                            raise TimeoutError(
                                f"기계적 HOME 이동이 {HOME_MOTION_TIMEOUT_SECONDS:.0f}초 "
                                "안에 끝나지 않았습니다."
                            )
                    finally:
                        d_type.SetQueuedCmdStopExec(api)
                        d_type.SetQueuedCmdClear(api)
                    result = {
                        "robot": robot_name,
                        "homed": True,
                        "queue_index": target_index,
                        "pose": _pose(d_type, api),
                    }
                responses.put(success_response(request_id, result))
            except Exception as exc:
                # 형식이 잘못된 요청도 가능한 경우 요청 번호를 돌려줘 원인을 찾기 쉽게 한다.
                possible_id = raw_request.get("id", 0) if isinstance(raw_request, dict) else 0
                response_id = possible_id if isinstance(possible_id, int) else 0
                responses.put(error_response(response_id, exc))
    except Exception as exc:
        responses.put({"event": "startup", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        # 정상·오류 종료 모두 포트를 해제해야 다른 프로그램이 다시 연결할 수 있다.
        if connected and d_type is not None and api is not None:
            try:
                # 예외 종료 시에도 남아 있을 수 있는 조그를 먼저 멈춘다.
                d_type.SetJOGCmd(api, 0, d_type.JC.JogIdle, 0)
            except Exception:
                pass
            try:
                d_type.DisconnectDobot(api)
            except Exception:
                pass


@dataclass
class DobotWorker:
    """메인 프로그램이 자식 Worker를 쉽게 제어하도록 만든 관리 클래스."""
    robot_name: str
    port: str
    baudrate: int = 115200
    sdk_dir: Path = DEFAULT_SDK_DIR

    def __post_init__(self) -> None:
        # Windows에서는 새 Python 프로세스를 만드는 spawn 방식을 사용한다.
        self._context = mp.get_context("spawn")
        self._commands: mp.Queue | None = None
        self._responses: mp.Queue | None = None
        self._stop_requested: Any = None
        self._stop_applied: Any = None
        self._process: mp.Process | None = None
        self._request_id = 0
        self.restart_count = 0
        # 8개 좌표와 마지막 갱신 시각 1개를 자식/메인 프로세스가 함께 사용한다.
        self._position_cache = self._context.Array("d", 9, lock=True)

    def _create_fresh_queues(self) -> None:
        """재시작 전 이전 응답이 남지 않은 새 명령·응답 Queue를 만든다."""
        self._close_queues()
        self._commands = self._context.Queue()
        self._responses = self._context.Queue()
        # Event는 일반 명령 Queue가 처리 중이어도 다른 스레드에서 켤 수 있는 신호다.
        self._stop_requested = self._context.Event()
        self._stop_applied = self._context.Event()

    def _close_queues(self) -> None:
        """더 이상 쓰지 않는 Queue의 내부 Windows 자원을 정리한다."""
        for message_queue in (self._commands, self._responses):
            if message_queue is not None:
                try:
                    message_queue.close()
                    message_queue.cancel_join_thread()
                except (OSError, ValueError):
                    pass
        self._commands = None
        self._responses = None
        self._stop_requested = None
        self._stop_applied = None

    def start(self, timeout: float = 8.0) -> dict[str, Any]:
        """Worker를 실행하고 제한 시간 안에 연결 결과를 받는다."""
        timeout = validate_timeout(timeout)
        if self._process is not None and self._process.is_alive():
            raise RuntimeError(f"{self.robot_name} Worker가 이미 실행 중입니다.")
        # 재시작일 수 있으므로 이전 메시지가 없는 새 Queue를 사용한다.
        self._create_fresh_queues()
        self._process = self._context.Process(
            target=_worker_main,
            name=f"{self.robot_name}-worker",
            args=(
                self.robot_name, self.port, self.baudrate, str(self.sdk_dir),
                self._commands, self._responses,
                self._stop_requested, self._stop_applied,
                self._position_cache,
            ),
        )
        self._process.start()
        try:
            response = self._responses.get(timeout=timeout)
        except queue.Empty as exc:
            self.terminate()
            raise TimeoutError(f"{self.robot_name} 연결이 {timeout:.1f}초 안에 응답하지 않았습니다.") from exc
        if not response.get("ok"):
            self.terminate()
            raise ConnectionError(f"{self.robot_name} 연결 실패: {response.get('error')}")
        return response

    def request(
        self,
        command: str | CommandName,
        timeout: float = 5.0,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Worker에 명령을 보내고 같은 요청 번호의 응답을 기다린다."""
        timeout = validate_timeout(timeout)
        if self._process is None or not self._process.is_alive():
            raise RuntimeError(f"{self.robot_name} Worker가 실행 중이 아닙니다.")
        if self._commands is None or self._responses is None:
            raise RuntimeError(f"{self.robot_name}의 통신 Queue가 준비되지 않았습니다.")
        # 허용되지 않은 명령은 Queue에 넣기 전에 메인 프로세스에서 차단한다.
        validated_command = CommandName.parse(command)
        # 요청 번호로 이전 요청의 늦은 응답과 현재 응답이 섞였는지 확인한다.
        self._request_id += 1
        request_id = self._request_id
        message = CommandMessage(request_id, validated_command, payload or {})
        self._commands.put(message.to_dict())
        try:
            response = self._responses.get(timeout=timeout)
        except queue.Empty as exc:
            # 이동 명령의 응답이 유실됐을 때 실제 로봇이 움직였는지 메인 쪽에서는
            # 알 수 없다. Worker를 끝내기 전에 별도 Event 경로로 정지를 먼저 요청한다.
            motion_commands = {
                CommandName.JOG_START,
                CommandName.JOG_KEEPALIVE,
                CommandName.JOG_FOR,
                CommandName.MOVE_PTP,
                CommandName.HOME,
            }
            if validated_command in motion_commands:
                try:
                    if self._stop_applied is not None and self._stop_requested is not None:
                        self._stop_applied.clear()
                        self._stop_requested.set()
                        # SDK 호출이 이미 반환 중이라면 Worker가 정지 명령을 적용할
                        # 짧은 기회를 준다. 재이동 명령은 절대 자동 전송하지 않는다.
                        self._stop_applied.wait(0.5)
                except (OSError, ValueError):
                    pass
            self.terminate()
            raise DobotCommandTimeout(
                self.robot_name, validated_command.value, timeout
            ) from exc
        parsed_response = ResponseMessage.from_dict(response)
        if parsed_response.request_id != request_id:
            self.terminate()
            raise RuntimeError(f"{self.robot_name} 응답 ID 불일치")
        if not parsed_response.ok:
            raise DobotCommandError(
                f"{self.robot_name}의 {validated_command.value} 명령 실패: "
                f"{parsed_response.error}"
            )
        return parsed_response.result

    def restart(self, connect_timeout: float = 8.0) -> dict[str, Any]:
        """문제가 생긴 Worker만 종료하고 새 프로세스로 다시 연결한다."""
        self.terminate()
        startup = self.start(timeout=connect_timeout)
        self.restart_count += 1
        return startup

    def request_with_recovery(
        self,
        command: str | CommandName,
        request_timeout: float = 5.0,
        connect_timeout: float = 8.0,
        retries: int = 1,
    ) -> Any:
        """읽기 명령 실패 시 Worker를 재시작하고 제한 횟수만 다시 시도한다.

        이동 명령은 통신이 끊긴 순간 실제 실행 여부를 알 수 없으므로 자동 재전송하면
        위험하다. 그래서 현재의 읽기 전용 명령만 이 복구 기능에서 허용한다.
        """
        validated_command = CommandName.parse(command)
        recoverable_commands = {
            CommandName.PING,
            CommandName.GET_IDENTITY,
            CommandName.GET_STATUS,
        }
        if validated_command not in recoverable_commands:
            raise ValueError(f"자동 복구가 허용되지 않은 명령: {validated_command.value}")
        if retries < 0:
            raise ValueError("retries는 0 이상이어야 합니다.")

        errors: list[str] = []
        for attempt in range(retries + 1):
            try:
                return self.request(validated_command, timeout=request_timeout)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                if attempt >= retries:
                    break
                try:
                    self.restart(connect_timeout=connect_timeout)
                except Exception as restart_error:
                    errors.append(f"재시작 실패: {type(restart_error).__name__}: {restart_error}")
                    break

        detail = " | ".join(errors)
        raise WorkerRecoveryError(
            f"{self.robot_name}의 {validated_command.value} 복구 실패: {detail}"
        )

    def ping(self, timeout: float = 5.0) -> dict[str, Any]:
        """Worker가 살아 있고 명령·응답 통신이 되는지 확인한다."""
        return self.request(CommandName.PING, timeout)

    def get_identity(self, timeout: float = 5.0) -> dict[str, Any]:
        """로봇 시리얼번호와 장치명을 읽는다."""
        return self.request(CommandName.GET_IDENTITY, timeout)

    def get_status(self, timeout: float = 5.0) -> dict[str, Any]:
        """현재 좌표, 관절각, 알람 상태를 읽는다."""
        return self.request(CommandName.GET_STATUS, timeout)

    def latest_position(self, max_age_seconds: float = 3.0) -> dict[str, float] | None:
        """Worker가 가장 최근에 읽은 X/Y/Z/R/J1~J4를 이동 없이 반환한다."""
        return read_position(self._position_cache, max_age_seconds)

    def jog_for(
        self,
        axis: str,
        direction: str,
        duration: float = 0.10,
        speed_percent: float = 5.0,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """짧은 시간만 저속 조그한다. 이동 명령이므로 자동 재시도하지 않는다."""
        jog = JogRequest.from_payload(
            {
                "axis": axis,
                "direction": direction,
                "duration": duration,
                "speed_percent": speed_percent,
            }
        )
        return self.request(CommandName.JOG_FOR, timeout, jog.to_payload())

    def start_jog(
        self,
        axis: str,
        direction: str,
        watchdog_seconds: float = 0.25,
        speed_percent: float = 5.0,
        timeout: float = 4.0,
    ) -> dict[str, Any]:
        """조그를 시작한다. 정지 명령이 없어도 watchdog 시간이 되면 자동 정지한다.

        USB 허브에 연결된 4대가 순간적으로 지연될 수 있어 시작 응답은 4초까지
        기다린다. 이 값은 명령 응답 대기시간이며, 실제 JOG 안전 감시는 계속
        watchdog_seconds의 기본값인 0.25초를 사용한다.
        """
        jog = JogRequest.from_payload(
            {
                "axis": axis,
                "direction": direction,
                "duration": watchdog_seconds,
                "speed_percent": speed_percent,
            }
        )
        return self.request(CommandName.JOG_START, timeout, jog.to_payload())

    def keepalive_jog(
        self, watchdog_seconds: float = 0.25, timeout: float = 1.0
    ) -> dict[str, Any]:
        """버튼을 누르는 동안 JOG 안전 감시 시간을 짧게 연장한다."""
        return self.request(
            CommandName.JOG_KEEPALIVE,
            timeout,
            {"watchdog_seconds": watchdog_seconds},
        )

    def stop_jog(self, timeout: float = 2.0) -> dict[str, Any]:
        """현재 조그를 즉시 정지한다."""
        return self.request(CommandName.JOG_STOP, timeout)

    def move_ptp(
        self,
        target: dict[str, float],
        speed_percent: float = 5.0,
        arrival_timeout: float = 10.0,
        tolerance: float = 0.25,
        allow_large_move: bool = False,
    ) -> dict[str, Any]:
        """절대 좌표 PTP 이동 후 실제 좌표 도착을 확인한다."""
        payload = dict(target)
        payload.update(
            speed_percent=speed_percent,
            timeout=arrival_timeout,
            tolerance=tolerance,
            allow_large_move=allow_large_move,
        )
        ptp = PTPRequest.from_payload(payload)
        # 응답 제한은 Worker 내부의 도착 확인 뒤 결과가 Queue로 돌아올 여유를 둔다.
        response_timeout = arrival_timeout + PTP_RESPONSE_MARGIN_SECONDS
        return self.request(CommandName.MOVE_PTP, response_timeout, payload)

    def clear_queue(self, timeout: float = 3.0) -> dict[str, Any]:
        """Dobot 컨트롤러에 대기 중인 큐 명령을 모두 삭제한다."""
        return self.request(CommandName.QUEUE_CLEAR, timeout)

    def start_queue(self, timeout: float = 3.0) -> dict[str, Any]:
        """Dobot 컨트롤러의 큐 실행을 시작한다."""
        return self.request(CommandName.QUEUE_START, timeout)

    def stop_queue(self, timeout: float = 3.0) -> dict[str, Any]:
        """현재 명령 이후의 큐 실행을 정지한다."""
        return self.request(CommandName.QUEUE_STOP, timeout)

    def force_stop(self, timeout: float = 1.0) -> dict[str, Any]:
        """진행 중인 일반 명령과 별도로 JOG·PTP·HOME 이동을 강제 정지한다.

        이 메서드는 응답 Queue를 사용하지 않으므로 다른 스레드가 PTP 결과를 기다리는
        중에도 호출할 수 있다. 하드웨어 비상정지 회로를 대신하는 기능은 아니다.
        """
        timeout = validate_timeout(timeout)
        if self._process is None or not self._process.is_alive():
            raise RuntimeError(f"{self.robot_name} Worker가 실행 중이 아닙니다.")
        if self._stop_requested is None or self._stop_applied is None:
            raise RuntimeError(f"{self.robot_name}의 강제 정지 신호가 준비되지 않았습니다.")
        self._stop_applied.clear()
        self._stop_requested.set()
        if not self._stop_applied.wait(timeout):
            raise DobotCommandTimeout(self.robot_name, "force_stop", timeout)
        return {"robot": self.robot_name, "stopped": True, "forced": True}

    def get_queue_index(self, timeout: float = 3.0) -> dict[str, Any]:
        """현재 실행됐거나 실행 중인 큐 명령 번호를 읽는다."""
        return self.request(CommandName.GET_QUEUE_INDEX, timeout)

    def set_end_effector(
        self, tool_type: str, action: str, timeout: float = 3.0
    ) -> dict[str, Any]:
        """그리퍼 또는 흡착컵에 검사된 동작 명령을 즉시 보낸다."""
        tool = EndEffectorRequest.from_payload(
            {"tool_type": tool_type, "action": action}
        )
        return self.request(
            CommandName.SET_END_EFFECTOR,
            timeout,
            {"tool_type": tool.tool_type, "action": tool.action},
        )

    def get_end_effector(
        self, tool_type: str, timeout: float = 3.0
    ) -> dict[str, Any]:
        """그리퍼 닫힘 또는 흡착 ON 상태를 읽는다."""
        return self.request(
            CommandName.GET_END_EFFECTOR, timeout, {"tool_type": tool_type}
        )

    def get_alarms(self, timeout: float = 3.0) -> dict[str, Any]:
        """현재 알람 번호와 16진수 표기를 읽는다."""
        return self.request(CommandName.GET_ALARMS, timeout)

    def get_home_params(self, timeout: float = 3.0) -> dict[str, Any]:
        """로봇을 움직이지 않고 현재 HOME 복귀 위치 X/Y/Z/R을 읽는다."""
        return self.request(CommandName.GET_HOME_PARAMS, timeout)

    def set_home_params(
        self, pose: dict[str, float], timeout: float = 3.0
    ) -> dict[str, Any]:
        """Dobot_1 또는 Dobot_2의 HOME 복귀 위치를 저장하고 다시 읽어 확인한다."""
        if self.robot_name not in HOME_PARAMETER_ROBOTS:
            raise ValueError("HOME 복귀 위치 변경은 Dobot_1과 Dobot_2만 허용됩니다.")
        checked = _home_pose_from_payload(pose)
        return self.request(CommandName.SET_HOME_PARAMS, timeout, checked)

    def clear_alarms(self, timeout: float = 3.0) -> dict[str, Any]:
        """조그·큐를 먼저 정지한 뒤 알람 표시를 초기화하고 다시 확인한다."""
        return self.request(CommandName.CLEAR_ALARMS, timeout)

    def home(
        self,
        timeout: float = HOME_MOTION_TIMEOUT_SECONDS + HOME_RESPONSE_MARGIN_SECONDS,
    ) -> dict[str, Any]:
        """컨트롤러의 기계적 HOME 절차를 실행하고 완료까지 기다린다."""
        return self.request(CommandName.HOME, timeout)

    def close(self, timeout: float = 2.0) -> None:
        """정상 종료를 먼저 요청하고, 응답하지 않을 때만 강제 종료한다."""
        process = self._process
        if process is None:
            return
        if process.is_alive():
            try:
                self.request(CommandName.SHUTDOWN, timeout=timeout)
            except Exception:
                pass
            process.join(timeout)
        if process.is_alive():
            process.terminate()
            process.join(timeout)
        if process.is_alive():
            # 이 오류는 포트 해제를 확실히 확인하지 못했다는 뜻이므로 숨기지 않는다.
            raise RuntimeError(f"{self.robot_name} Worker 프로세스를 종료하지 못했습니다.")
        self._process = None
        self._close_queues()

    @property
    def is_running(self) -> bool:
        """Worker 프로세스가 현재 살아 있는지 True/False로 알려준다."""
        return self._process is not None and self._process.is_alive()

    def terminate(self) -> None:
        """SDK가 멈췄을 때 해당 Dobot의 Worker 프로세스만 종료한다."""
        process = self._process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(2.0)
        self._process = None
        self._close_queues()

    def __enter__(self) -> "DobotWorker":
        """with 문으로 사용할 때 Worker를 자동 시작한다."""
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        """with 문을 빠져나갈 때 오류 여부와 관계없이 연결을 정리한다."""
        self.close()
