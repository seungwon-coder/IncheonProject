"""Dobot_1을 현재 위치에서 조금 이동시켜 PTP 도착 확인을 시험한다."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys

from robot_control.dobot_config import ROBOT_NAMES, load_config
from robot_control.dobot_worker import DobotWorker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dobot 저속 PTP 도착 시험")
    parser.add_argument("--robot", choices=ROBOT_NAMES, default="Dobot_1")
    parser.add_argument("--axis", choices=("x", "y", "z", "r"), default="r")
    parser.add_argument("--delta", type=float, default=0.5)
    parser.add_argument("--speed", type=float, default=5.0)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    """현재 좌표를 읽고 한 축의 작은 상대 이동을 절대 PTP 목표로 변환한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if abs(args.delta) > 5.0 or args.delta == 0:
        print("오류: 이동량은 0이 아니며 -5~5 범위여야 합니다.", file=sys.stderr)
        return 2
    item = load_config(require_all_ports=True)["robots"][args.robot]
    print(
        f"시험 조건: {args.robot} {item['port']} / {args.axis.upper()} "
        f"{args.delta:+.2f} / 속도 {args.speed:.1f}%"
    )
    if not args.execute:
        print("DRY-RUN 완료: 현재 좌표를 읽거나 로봇을 이동하지 않았습니다.")
        return 0
    worker = DobotWorker(args.robot, item["port"], int(item["baudrate"]))
    try:
        worker.start()
        status = worker.get_status()
        if status["alarms"]:
            print(f"실행 차단: 현재 알람 {status['alarms']}", file=sys.stderr)
            return 3
        target = dict(status["pose"])
        target[args.axis] += args.delta
        result = worker.move_ptp(target, args.speed)
        print(f"이동 전 좌표: {result['before']}")
        print(f"목표 좌표: {result['target']}")
        print(f"도착 좌표: {result['after']}")
        print("PTP 이동 및 3회 연속 도착 확인 완료")
        return 0
    except Exception as exc:
        print(f"시험 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        worker.close()


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
