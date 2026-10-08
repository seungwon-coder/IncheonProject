"""한 Dobot Worker 장애가 나머지 로봇에 전파되지 않는지 검사한다.

Dobot_1의 Python Worker 프로세스만 의도적으로 종료한다. 로봇 자체에는 이동이나
정지 명령을 보내지 않는다. 이후 상태 읽기를 실행하여 Dobot_1은 새 Worker로
복구되고 Dobot_2~4는 기존 Worker를 유지하는지 확인한다.
"""

from __future__ import annotations

import multiprocessing as mp
import sys

from robot_control.dobot_fleet import DobotFleet, RobotResult


def require_all_success(results: dict[str, RobotResult], stage: str) -> None:
    """한 대라도 실패하면 어떤 단계에서 실패했는지 설명하는 오류를 만든다."""
    failures = [f"{name}: {result.error}" for name, result in results.items() if not result.ok]
    if failures:
        raise RuntimeError(f"{stage} 실패 - " + " | ".join(failures))


def main() -> int:
    """4대 연결 → PID 기록 → Dobot_1 Worker 종료 → 복구·격리 확인 순서."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    fleet = DobotFleet()
    try:
        connected = fleet.connect_all()
        require_all_success(connected, "연결")

        before = fleet.ping_all()
        require_all_success(before, "장애 전 PING")
        before_pids = {
            name: int(result.data["process_id"]) for name, result in before.items()
        }
        print("장애 전 Worker PID:", before_pids)

        # 실제 Dobot 전원이나 통신선은 건드리지 않고 Worker 하나만 종료한다.
        fleet.terminate_one_for_test("Dobot_1")
        print("Dobot_1 Worker만 시험용으로 종료했습니다.")

        after = fleet.read_all_status()
        require_all_success(after, "장애 후 상태 읽기")
        after_pids = {
            name: int(result.data["process_id"]) for name, result in after.items()
        }
        print("복구 후 Worker PID:", after_pids)

        if after_pids["Dobot_1"] == before_pids["Dobot_1"]:
            raise RuntimeError("Dobot_1 Worker가 새 프로세스로 복구되지 않았습니다.")
        for name in ("Dobot_2", "Dobot_3", "Dobot_4"):
            if after_pids[name] != before_pids[name]:
                raise RuntimeError(f"{name} Worker가 불필요하게 재시작됐습니다.")

        print("격리 성공: Dobot_1만 복구됐고 Dobot_2~4 Worker는 유지됐습니다.")
        return 0
    except Exception as exc:
        print(f"격리 시험 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        closed = fleet.close_all()
        for name, result in closed.items():
            print(f"{name}: {'종료 성공' if result.ok else result.error}")


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
