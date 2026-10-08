"""설정된 로봇의 그리퍼 또는 흡착컵 공통 인터페이스를 시험한다."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time

from robot_control.dobot_config import ROBOT_NAMES, load_config
from robot_control.dobot_worker import DobotWorker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dobot 엔드이펙터 시험")
    parser.add_argument("--robot", choices=ROBOT_NAMES, default="Dobot_1")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    """설정에 맞춰 그리퍼 열기·닫기 또는 흡착 ON·OFF를 짧게 확인한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    item = load_config(require_all_ports=True)["robots"][args.robot]
    tool_type = item["end_effector"]
    print(f"시험 대상: {args.robot} {item['port']} / {tool_type}")
    if not args.execute:
        print("DRY-RUN 완료: 엔드이펙터 출력은 동작하지 않았습니다.")
        return 0

    worker = DobotWorker(args.robot, item["port"], int(item["baudrate"]))
    try:
        worker.start()
        if worker.get_status()["alarms"]:
            print("실행 차단: 로봇에 알람이 있습니다.", file=sys.stderr)
            return 3
        if tool_type == "gripper":
            first_action, second_action = "open", "close"
        else:
            first_action, second_action = "on", "off"
        first = worker.set_end_effector(tool_type, first_action)
        time.sleep(0.7)
        first_state = worker.get_end_effector(tool_type)
        second = worker.set_end_effector(tool_type, second_action)
        time.sleep(0.7)
        second_state = worker.get_end_effector(tool_type)
        disabled = worker.set_end_effector(tool_type, "disable")
        print(f"{first_action}: 명령={first['on']}, 상태={first_state['on']}")
        print(f"{second_action}: 명령={second['on']}, 상태={second_state['on']}")
        print(f"disable: enabled={disabled['enabled']}")
        print("엔드이펙터 공통 인터페이스 시험 완료")
        return 0
    except Exception as exc:
        print(f"시험 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            if worker.is_running:
                worker.set_end_effector(tool_type, "disable")
        except Exception:
            pass
        worker.close()


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
