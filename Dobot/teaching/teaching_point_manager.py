"""티칭 파일 초기화·조회와 현재 로봇 좌표 저장을 수행하는 CLI."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys

from robot_control.dobot_config import ROBOT_NAMES, load_config
from robot_control.dobot_worker import DobotWorker
from teaching.teaching_points import (
    DEFAULT_TEACHING_FILE,
    TeachingPointError,
    load_teaching_data,
    missing_points,
    new_teaching_data,
    save_teaching_data,
    teach_point,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dobot 티칭 포인트 관리자")
    parser.add_argument("--init", action="store_true", help="미등록 티칭 파일 생성")
    parser.add_argument("--list", action="store_true", help="현재 등록 상태 표시")
    parser.add_argument("--capture", nargs=2, metavar=("ROBOT", "POINT"))
    parser.add_argument("--speed", type=float, default=5.0)
    parser.add_argument("--save", action="store_true", help="현재 좌표를 실제 JSON에 저장")
    return parser.parse_args()


def print_points(data: dict[str, object]) -> None:
    """초보자도 확인하기 쉽게 로봇별 등록 상태를 한 줄씩 표시한다."""
    robots = data["robots"]
    assert isinstance(robots, dict)
    for robot_name in ROBOT_NAMES:
        print(f"[{robot_name}]")
        for point_name, point in robots[robot_name].items():
            state = "등록" if point["valid"] else "미등록"
            print(f"  {point_name:<22} {state}  좌표={point['pose']}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        if args.init:
            if DEFAULT_TEACHING_FILE.exists():
                raise TeachingPointError(
                    "기존 티칭 파일이 있어 초기화하지 않았습니다. 좌표를 보존합니다."
                )
            save_teaching_data(new_teaching_data())
            print(f"미등록 티칭 파일 생성: {DEFAULT_TEACHING_FILE}")

        data = load_teaching_data()
        if args.capture:
            robot_name, point_name = args.capture
            if robot_name not in ROBOT_NAMES:
                raise TeachingPointError(f"등록되지 않은 로봇: {robot_name}")
            # 포인트 이름은 연결 전에 먼저 확인해 잘못된 입력으로 포트를 점유하지 않는다.
            if point_name not in data["robots"][robot_name]:
                raise TeachingPointError(f"등록되지 않은 포인트: {robot_name}/{point_name}")
            if not args.save:
                print("DRY-RUN: --save가 없어 현재 좌표를 읽거나 저장하지 않았습니다.")
                return 0
            config = load_config(require_all_ports=True)
            item = config["robots"][robot_name]
            worker = DobotWorker(robot_name, item["port"], int(item["baudrate"]))
            try:
                worker.start()
                status = worker.get_status()
                if status["alarms"]:
                    raise TeachingPointError(f"현재 알람으로 티칭 차단: {status['alarms']}")
                updated = teach_point(data, robot_name, point_name, status["pose"], args.speed)
                save_teaching_data(updated)
                print(f"저장 완료: {robot_name}/{point_name} = {status['pose']}")
                data = updated
            finally:
                worker.close()

        if args.list or not (args.init or args.capture):
            print_points(data)
            print(f"미등록 필수 포인트: {len(missing_points(data))}개")
        return 0
    except (TeachingPointError, OSError, ValueError) as exc:
        print(f"티칭 오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
