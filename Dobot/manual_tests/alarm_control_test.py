"""등록된 Dobot 4대의 알람 조회와 초기화를 차례로 확인한다."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
from pathlib import Path

# 권장 실행법은 프로젝트 루트에서
# ``python -m manual_tests.alarm_control_test``이다. 다만 초보자가 이 파일을
# 직접 실행해도 robot_control 패키지를 찾을 수 있도록 프로젝트 루트를 추가한다.
if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from robot_control.dobot_config import ROBOT_NAMES, load_config
from robot_control.dobot_worker import DobotWorker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dobot 알람 조회·초기화 시험")
    parser.add_argument("--robot", choices=(*ROBOT_NAMES, "all"), default="all")
    parser.add_argument("--clear", action="store_true", help="조회 후 알람 초기화")
    return parser.parse_args()


def main() -> int:
    """로봇별로 연결하여 알람을 읽고 요청된 경우 초기화 결과를 재확인한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    config = load_config(require_all_ports=True)
    names = ROBOT_NAMES if args.robot == "all" else (args.robot,)
    failed = False
    for name in names:
        item = config["robots"][name]
        worker = DobotWorker(name, item["port"], int(item["baudrate"]))
        try:
            worker.start()
            alarms = worker.get_alarms()
            print(f"{name} {item['port']}: 알람={alarms['details'] or '없음'}")
            if args.clear:
                result = worker.clear_alarms()
                print(
                    f"{name}: 초기화 전={result['before'] or '없음'}, "
                    f"초기화 후={result['after'] or '없음'}"
                )
                if not result["cleared"]:
                    failed = True
                    print(f"{name}: 원인이 남아 알람이 다시 발생했습니다.", file=sys.stderr)
        except Exception as exc:
            failed = True
            print(f"{name}: 시험 실패 - {type(exc).__name__}: {exc}", file=sys.stderr)
        finally:
            worker.close()
    return 1 if failed else 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
