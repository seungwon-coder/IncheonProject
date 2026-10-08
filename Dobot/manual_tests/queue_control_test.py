"""이동 명령이 없는 빈 큐로 시작·정지·초기화 기능을 확인한다."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys

from robot_control.dobot_config import load_config
from robot_control.dobot_worker import DobotWorker


def parse_args() -> argparse.Namespace:
    """실제 컨트롤러 명령은 --execute가 있을 때만 전송한다."""
    parser = argparse.ArgumentParser(description="Dobot_1 빈 큐 제어 시험")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    """빈 큐 초기화 → 시작 → 인덱스 확인 → 정지 → 초기화 순서로 시험한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    item = load_config(require_all_ports=True)["robots"]["Dobot_1"]
    print(f"시험 대상: Dobot_1 {item['port']} (이동 명령 없는 빈 큐)")
    if not args.execute:
        print("DRY-RUN 완료: 컨트롤러에 명령을 전송하지 않았습니다.")
        return 0

    worker = DobotWorker("Dobot_1", item["port"], int(item["baudrate"]))
    try:
        worker.start()
        status = worker.get_status()
        if status["alarms"]:
            print(f"실행 차단: 현재 알람 {status['alarms']}", file=sys.stderr)
            return 3
        before = status["pose"]
        first_clear = worker.clear_queue()
        started = worker.start_queue()
        index = worker.get_queue_index()
        stopped = worker.stop_queue()
        final_clear = worker.clear_queue()
        after = worker.get_status()["pose"]
        print(f"첫 초기화: {first_clear['cleared']}")
        print(f"큐 시작: {started['started']}")
        print(f"현재 큐 인덱스: {index['current_index']}")
        print(f"큐 정지: {stopped['stopped']}")
        print(f"마지막 초기화: {final_clear['cleared']}")
        print(f"시험 전 좌표: {before}")
        print(f"시험 후 좌표: {after}")
        print("빈 큐 시작·정지·초기화 시험 완료")
        return 0
    except Exception as exc:
        print(f"시험 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        # 중간 실패 시에도 가능한 범위에서 큐를 정지하고 연결을 해제한다.
        try:
            if worker.is_running:
                worker.stop_queue()
                worker.clear_queue()
        except Exception:
            pass
        worker.close()


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
