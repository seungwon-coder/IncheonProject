"""등록된 Dobot 4대의 상태를 동시에 읽는 무동작 테스트.

각 Worker를 모두 연결 상태로 유지한 뒤 좌표와 알람을 읽는다. 이를 통해 한 대씩
연결할 때뿐 아니라 4대를 동시에 사용할 때도 통신이 안정적인지 확인한다.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from robot_control.dobot_config import ConfigError, DEFAULT_CONFIG, ROBOT_NAMES, load_config
from robot_control.dobot_protocol import CommandName
from robot_control.dobot_worker import DobotWorker


def parse_args() -> argparse.Namespace:
    """설정 파일 위치와 통신 제한 시간을 실행 옵션으로 받는다."""
    parser = argparse.ArgumentParser(description="등록된 Dobot 4대의 상태를 동시에 읽습니다.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--connect-timeout", type=float, default=8.0)
    parser.add_argument("--read-timeout", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    """설정 확인 → 4대 연결 → 상태 읽기 → 전체 연결 해제 순서로 실행한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    # 연결에 성공한 Worker만 보관하고 마지막 finally에서 모두 안전하게 닫는다.
    workers: list[DobotWorker] = []
    process_ids: set[int] = set()
    failures: list[str] = []
    try:
        # 하나라도 포트가 미등록이면 장비 혼동을 막기 위해 시작하지 않는다.
        config = load_config(args.config, require_all_ports=True)
        print("Dobot Worker 연결 시작 (이동 명령 없음)")
        # 각 Worker는 별도 프로세스이므로 네 대가 동시에 연결 상태를 유지한다.
        for name in ROBOT_NAMES:
            item = config["robots"][name]
            worker = DobotWorker(name, item["port"], int(item["baudrate"]))
            try:
                info = worker.start(timeout=args.connect_timeout)
                workers.append(worker)
                process_id = int(info["process_id"])
                if process_id in process_ids:
                    failures.append(f"{name}: Worker 프로세스 ID {process_id} 중복")
                process_ids.add(process_id)
                print(f"{name}: {info['port']} 연결 성공 / Worker PID={process_id}")
            except Exception as exc:
                failures.append(f"{name}: {exc}")
                print(f"{name}: 연결 실패 - {exc}")

        print("\n상태 읽기")
        # 좌표를 변경하지 않고 현재 값과 알람만 읽는다.
        for worker in workers:
            started = time.perf_counter()
            try:
                # ping으로 명령 전달과 응답 반환 경로가 먼저 정상인지 확인한다.
                ping = worker.request_with_recovery(
                    CommandName.PING,
                    request_timeout=args.read_timeout,
                    connect_timeout=args.connect_timeout,
                    retries=1,
                )
                if ping["robot"] != worker.robot_name or ping["port"] != worker.port:
                    raise RuntimeError(f"PING 응답 대상 불일치: {ping}")
                status = worker.request_with_recovery(
                    CommandName.GET_STATUS,
                    request_timeout=args.read_timeout,
                    connect_timeout=args.connect_timeout,
                    retries=1,
                )
                elapsed_ms = (time.perf_counter() - started) * 1000
                pose = status["pose"]
                joints = status["joints"]
                print(
                    f"{worker.robot_name}: X={pose['x']:.2f}, Y={pose['y']:.2f}, "
                    f"Z={pose['z']:.2f}, R={pose['r']:.2f}, "
                    f"J1={joints[0]:.2f}, J2={joints[1]:.2f}, "
                    f"J3={joints[2]:.2f}, J4={joints[3]:.2f}, "
                    f"알람={status['alarms'] or '없음'}, "
                    f"PING+상태 응답={elapsed_ms:.0f}ms"
                )
            except Exception as exc:
                failures.append(f"{worker.robot_name} 상태: {exc}")
                print(f"{worker.robot_name}: 상태 읽기 실패 - {exc}")
    except (ConfigError, OSError) as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        print("예: python .\\dobot_config.py --assign Dobot_1 COM14", file=sys.stderr)
        return 2
    finally:
        # 중간에 오류가 나도 열린 COM 포트를 반드시 해제한다.
        for worker in workers:
            try:
                worker.close()
                if worker.is_running:
                    failures.append(f"{worker.robot_name}: Worker 종료 확인 실패")
                else:
                    print(f"{worker.robot_name}: 연결 해제 및 Worker 종료 확인")
            except Exception as exc:
                failures.append(f"{worker.robot_name} 종료: {exc}")

    # 4대가 성공했다면 네 Worker의 PID도 정확히 네 개여야 한다.
    if len(workers) == len(ROBOT_NAMES) and len(process_ids) != len(ROBOT_NAMES):
        failures.append(
            f"Worker 프로세스 분리 실패: 연결 {len(workers)}대, 고유 PID {len(process_ids)}개"
        )

    if failures:
        print("\n실패 항목:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\n4대 상태 읽기 성공")
    return 0


if __name__ == "__main__":
    # Windows multiprocessing 프로그램을 안전하게 시작하기 위한 필수 처리다.
    mp_freeze_support = getattr(__import__("multiprocessing"), "freeze_support")
    mp_freeze_support()
    raise SystemExit(main())
