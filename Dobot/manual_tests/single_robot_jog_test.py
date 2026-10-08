"""Dobot_1을 아주 짧게 저속 조그하여 방향을 확인하는 수동 시험 프로그램."""

from __future__ import annotations

import argparse
import sys

from robot_control.dobot_config import ROBOT_NAMES, load_config
from robot_control.dobot_jog import JogRequest
from robot_control.dobot_worker import DobotWorker


def parse_args() -> argparse.Namespace:
    """안전 확인 없이 이동하지 않도록 --execute를 필수 선택값으로 둔다."""
    parser = argparse.ArgumentParser(description="Dobot 저속 조그 안전 시험")
    parser.add_argument("--robot", choices=ROBOT_NAMES, default="Dobot_1")
    parser.add_argument("--axis", choices=("x", "y", "z", "r"), required=True)
    parser.add_argument("--direction", choices=("+", "-"), required=True)
    parser.add_argument("--duration", type=float, default=0.10)
    parser.add_argument("--speed", type=float, default=5.0, help="속도 비율(1~10%%)")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="주변 간섭과 비상정지 준비를 확인한 경우에만 실제 이동",
    )
    return parser.parse_args()


def main() -> int:
    """설정된 Dobot_1에 연결하고, 알람이 없을 때만 한 번 조그한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        jog = JogRequest.from_payload(
            {
                "axis": args.axis,
                "direction": args.direction,
                "duration": args.duration,
                "speed_percent": args.speed,
            }
        )
        config = load_config(require_all_ports=True)
        item = config["robots"][args.robot]
        print(
            f"시험 조건: {args.robot} {item['port']} / {jog.axis.upper()}{jog.direction} / "
            f"{jog.duration:.2f}초 / {jog.speed_percent:.1f}%"
        )
        if not args.execute:
            print("DRY-RUN 완료: 실제 이동은 하지 않았습니다.")
            print("주변 간섭 제거와 비상정지 준비 후 --execute를 추가하세요.")
            return 0

        worker = DobotWorker(args.robot, item["port"], int(item["baudrate"]))
        try:
            worker.start()
            status = worker.get_status()
            if status["alarms"]:
                print(f"실행 차단: 현재 알람 {status['alarms']}", file=sys.stderr)
                return 3
            result = worker.jog_for(
                jog.axis, jog.direction, jog.duration, jog.speed_percent
            )
            print(f"이동 전 좌표: {result['before']}")
            print(f"이동 후 좌표: {result['after']}")
            print("저속 조그 시험 완료")
            return 0
        finally:
            worker.close()
    except Exception as exc:
        print(f"시험 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
