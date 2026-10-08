"""6단계 HOME 및 안전 이동 순서를 로봇 한 대씩 확인한다.

기본 실행은 계획만 출력하며 Dobot에 연결하거나 움직이지 않는다. 실제 검증은
--execute와 로봇 이름 재입력을 모두 통과해야 시작된다.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp

from robot_control.dobot_config import ROBOT_NAMES, load_config
from robot_control.dobot_worker import DobotWorker
from robot_control.robot_motion_controller import ALLOWED_DESTINATIONS, RobotMotionController
from teaching.teaching_points import load_teaching_data


class PreviewWorker:
    """계획 확인 중 실수로 실제 통신이 일어나지 않게 막는 빈 Worker이다."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HOME 및 안전 이동 순서 단독 검증")
    parser.add_argument("--robot", choices=ROBOT_NAMES, default="Dobot_1")
    parser.add_argument("--mode", choices=("home", "cycle"), default="home")
    parser.add_argument(
        "--destination",
        help="cycle 모드의 조립 또는 반환 위치(예: assembly_1, return_1)",
    )
    parser.add_argument(
        "--skip-mechanical-home",
        action="store_true",
        help="기계적 HOME은 생략하고 사용자 ready 포인트부터 이동",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="주변 간섭과 비상정지 준비를 확인한 경우에만 실제 이동",
    )
    return parser.parse_args()


def selected_destination(robot_name: str, requested: str | None) -> str:
    """로봇별 허용 목적지 중 사용자가 선택한 값을 검사한다."""
    allowed = sorted(ALLOWED_DESTINATIONS[robot_name])
    destination = requested or allowed[0]
    if destination not in allowed:
        raise ValueError(
            f"{robot_name} 목적지는 {', '.join(allowed)} 중 하나여야 합니다."
        )
    return destination


def main() -> int:
    args = parse_args()
    config = load_config(require_all_ports=True)
    teaching = load_teaching_data()
    destination = selected_destination(args.robot, args.destination)
    preview = RobotMotionController(
        args.robot, PreviewWorker(), config=config, teaching_data=teaching
    )
    steps = (
        preview.build_initial_plan()
        if args.mode == "home"
        else preview.build_initial_plan() + preview.build_cycle_plan(destination)
    )

    print(f"대상: {args.robot} / 모드: {args.mode}")
    print("검증할 순서:")
    for number, description in enumerate(preview.preview(steps), start=1):
        print(f"  {number}. {description}")

    if not args.execute:
        print("DRY-RUN 완료: 실제 로봇에는 연결하거나 명령을 보내지 않았습니다.")
        return 0

    typed = input(f"실제 저속 검증을 시작하려면 {args.robot}을 정확히 입력하세요: ").strip()
    if typed != args.robot:
        print("입력이 일치하지 않아 실제 이동을 취소했습니다.")
        return 2

    item = config["robots"][args.robot]
    worker = DobotWorker(args.robot, item["port"], int(item["baudrate"]))
    try:
        worker.start()
        controller = RobotMotionController(
            args.robot, worker, config=config, teaching_data=teaching
        )
        controller.initialize(mechanical_home=not args.skip_mechanical_home)
        print(f"사용자 READY 대기 완료: {controller.waiting_point}")
        if args.mode == "cycle":
            controller.run_cycle(destination)
            print(f"공정 후 사용자 READY 대기 완료: {controller.waiting_point}")
        return 0
    except Exception as exc:
        try:
            worker.force_stop()
        except Exception:
            pass
        print(f"검증 실패: {type(exc).__name__}: {exc}")
        return 1
    finally:
        worker.close()


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
