"""Dobot_1의 조그 시작과 별도 즉시 정지 명령을 확인하는 수동 시험."""

from __future__ import annotations

import argparse
import sys
import time

from robot_control.dobot_config import ROBOT_NAMES, load_config
from robot_control.dobot_worker import DobotWorker


def parse_args() -> argparse.Namespace:
    """기본 실행은 dry-run이며 --execute가 있을 때만 실제 로봇을 움직인다."""
    parser = argparse.ArgumentParser(description="Dobot JOG 즉시 정지 시험")
    parser.add_argument("--robot", choices=ROBOT_NAMES, default="Dobot_1")
    parser.add_argument("--axis", choices=("x", "y", "z", "r"), default="r")
    parser.add_argument("--direction", choices=("+", "-"), default="+")
    parser.add_argument("--stop-after", type=float, default=0.08)
    parser.add_argument("--watchdog", type=float, default=0.25)
    parser.add_argument("--speed", type=float, default=5.0)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    """조그 시작 후 지정 시간에 별도의 JOG_STOP 명령을 보내고 좌표를 비교한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if not 0.01 <= args.stop_after < args.watchdog:
        print("오류: stop-after는 0.01초 이상이며 watchdog보다 짧아야 합니다.", file=sys.stderr)
        return 2

    config = load_config(require_all_ports=True)
    item = config["robots"][args.robot]
    print(
        f"시험 조건: {args.robot} {item['port']} / {args.axis.upper()}{args.direction} / "
        f"{args.speed:.1f}% / {args.stop_after:.2f}초 후 즉시 정지 / "
        f"감시 한계 {args.watchdog:.2f}초"
    )
    if not args.execute:
        print("DRY-RUN 완료: 실제 이동은 하지 않았습니다.")
        return 0

    worker = DobotWorker(args.robot, item["port"], int(item["baudrate"]))
    try:
        worker.start()
        status = worker.get_status()
        if status["alarms"]:
            print(f"실행 차단: 현재 알람 {status['alarms']}", file=sys.stderr)
            return 3
        started = worker.start_jog(
            args.axis, args.direction, args.watchdog, args.speed
        )
        time.sleep(args.stop_after)
        stopped = worker.stop_jog()
        print(f"이동 전 좌표: {started['before']}")
        print(f"정지 후 좌표: {stopped['pose']}")
        print("별도 JOG_STOP 명령 처리 완료")
        return 0
    except Exception as exc:
        # 가능한 경우 한 번 더 정지를 요청한다. 실패하더라도 Worker의 watchdog과
        # finally 정지가 추가 안전장치로 동작한다.
        try:
            if worker.is_running:
                worker.stop_jog()
        except Exception:
            pass
        print(f"시험 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        worker.close()


if __name__ == "__main__":
    import multiprocessing as mp

    mp.freeze_support()
    raise SystemExit(main())
