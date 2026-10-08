"""Dobot_1·2의 HOME 복귀 위치를 조회하거나 현재 자세로 저장한다.

기본 실행은 읽기만 수행한다. 실제 설정 변경은 --set-current와 --execute를 모두
지정하고 로봇 이름까지 다시 입력해야 하므로 실수로 값이 바뀌는 것을 막는다.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys

from robot_control.dobot_config import load_config
from robot_control.dobot_worker import DobotWorker


ALLOWED_ROBOTS = ("Dobot_1", "Dobot_2")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HOME 복귀 위치 조회·저장 시험")
    parser.add_argument("--robot", choices=ALLOWED_ROBOTS, required=True)
    parser.add_argument(
        "--set-current",
        action="store_true",
        help="현재 자세를 새 HOME 복귀 위치로 저장",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제 설정 변경 승인(조회만 할 때는 필요 없음)",
    )
    return parser.parse_args()


def main() -> int:
    """HOME 값과 현재 좌표를 읽고, 명시적으로 승인된 경우에만 저장한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    config = load_config(require_all_ports=True)
    item = config["robots"][args.robot]
    worker = DobotWorker(args.robot, item["port"], int(item["baudrate"]))
    try:
        worker.start()
        before = worker.get_home_params()["pose"]
        status = worker.get_status()
        current = status["pose"]
        print(f"대상: {args.robot} / 포트: {item['port']}")
        print(f"기존 HOME 복귀 위치: {before}")
        print(f"현재 로봇 자세: {current}")

        if not args.set_current:
            print("조회 완료: 설정을 변경하지 않았습니다.")
            return 0
        if not args.execute:
            print("실제 저장에는 --execute가 필요합니다. 설정을 변경하지 않았습니다.")
            return 2

        typed = input(
            f"현재 자세를 HOME 복귀 위치로 저장하려면 {args.robot}을 입력하세요: "
        ).strip()
        if typed != args.robot:
            print("입력이 일치하지 않아 설정 변경을 취소했습니다.")
            return 2

        result = worker.set_home_params(current)
        after = worker.get_home_params()["pose"]
        print(f"저장 결과: {result}")
        print(f"재조회 HOME 복귀 위치: {after}")
        print("저장 완료: 이 시험에서는 HOME 이동을 실행하지 않았습니다.")
        return 0
    except Exception as exc:
        # 작업자용 검사에서는 긴 내부 traceback보다 대상과 원인을 바로 보여준다.
        print(
            f"{args.robot} HOME 조회·저장 실패: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        try:
            worker.close()
        except Exception as exc:
            print(f"{args.robot} 연결 종료 실패: {exc}", file=sys.stderr)


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
