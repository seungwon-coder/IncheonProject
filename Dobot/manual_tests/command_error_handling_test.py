"""잘못된 명령이 안전하게 거부되고 Worker가 계속 동작하는지 확인한다."""

from __future__ import annotations

import multiprocessing as mp
import sys

from robot_control.dobot_config import load_config
from robot_control.dobot_errors import DobotCommandError
from robot_control.dobot_worker import DobotWorker


def main() -> int:
    """큰 PTP 이동을 일부러 요청하되 SDK 이동 전에 차단되는지 확인한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    item = load_config(require_all_ports=True)["robots"]["Dobot_1"]
    worker = DobotWorker("Dobot_1", item["port"], int(item["baudrate"]))
    try:
        worker.start()
        before = worker.get_status()
        unsafe_target = dict(before["pose"])
        unsafe_target["x"] += 100.0
        try:
            worker.move_ptp(unsafe_target, speed_percent=5)
            print("실패: 위험한 PTP 명령이 거부되지 않았습니다.", file=sys.stderr)
            return 1
        except DobotCommandError as exc:
            print(f"예상된 안전 거부: {exc}")

        # 오류 한 번이 Worker 전체를 망가뜨리지 않았는지 다시 상태를 읽는다.
        after = worker.get_status()
        if before["pose"] != after["pose"]:
            print("실패: 거부된 명령 뒤 좌표가 변했습니다.", file=sys.stderr)
            return 1
        print(f"오류 후 상태 조회 정상: {after['pose']}")
        print("명령 오류 격리 시험 완료 (로봇 이동 없음)")
        return 0
    except Exception as exc:
        print(f"시험 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        worker.close()


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
