"""통합 GUI와 같은 실제 공정 계층으로 선택 로봇 1사이클을 검증한다."""

import argparse
import multiprocessing as mp

from robot_control.dobot_fleet import DobotFleet
from robot_control.live_process_manager import LiveProcessManager
from robot_control.process_state_machine import ProcessSignals
from robot_control.process_state_machine import DOBOT_4_DESTINATIONS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot", default="Dobot_1")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--work-count",
        type=int,
        choices=(1, 2),
        default=1,
        help="Dobot_2 작업 횟수. 2이면 assembly_1 후 assembly_2를 실행합니다.",
    )
    parser.add_argument(
        "--product-type",
        choices=tuple(DOBOT_4_DESTINATIONS),
        help="Dobot_4 반환 제품 종류",
    )
    args = parser.parse_args()
    if args.work_count == 2 and args.robot != "Dobot_2":
        parser.error("--work-count 2는 Dobot_2에서만 사용할 수 있습니다.")
    if args.robot == "Dobot_4" and args.product_type is None:
        parser.error("Dobot_4는 --product-type으로 반환 종류를 지정해야 합니다.")
    if args.robot != "Dobot_4" and args.product_type is not None:
        parser.error("--product-type은 Dobot_4에서만 사용할 수 있습니다.")
    if not args.execute:
        print("실제 실행에는 --execute가 필요합니다.")
        return 2
    typed = input(
        f"실제 {args.work_count}사이클을 실행하려면 {args.robot}을 입력하세요: "
    ).strip()
    if typed != args.robot:
        print("입력이 일치하지 않아 취소했습니다.")
        return 2

    fleet = DobotFleet()
    try:
        connected = fleet.connect_all()
        if not all(result.ok for result in connected.values()):
            raise RuntimeError(f"4대 연결 실패: {connected}")
        manager = LiveProcessManager(fleet)
        status = manager.accept_current_waiting_pose(args.robot)
        print(f"안전 대기 좌표 확인: {status['pose']}")
        # 각 로봇의 실제 기동 인터록과 동일한 신호 조합을 사용한다.
        if args.robot in {"Dobot_1", "Dobot_2"}:
            signals = ProcessSignals(
                supply_complete=True,
                vision_ok=True,
                work_count=args.work_count,
            )
        elif args.robot == "Dobot_3":
            signals = ProcessSignals(vision_ok=True, agv_supply_complete=True)
        else:
            signals = ProcessSignals(
                product_arrived=True,
                product_type=args.product_type,
            )
        first_result = manager.process(args.robot, signals)
        if first_result is None:
            raise RuntimeError(f"{args.robot}의 기동 인터록이 충족되지 않았습니다.")
        if args.work_count == 2:
            print("첫 번째 공급 작업 assembly_1 완료")
            # 실제 PLC 신호처럼 공급 완료를 OFF로 복귀시킨 후 두 번째 상승 에지를 만든다.
            manager.process(
                args.robot,
                ProcessSignals(supply_complete=False, vision_ok=True, work_count=2),
            )
            print("공급 완료 신호 OFF 복귀 확인")
            second_result = manager.process(
                args.robot,
                ProcessSignals(supply_complete=True, vision_ok=True, work_count=2),
            )
            if second_result is None:
                raise RuntimeError("Dobot_2 두 번째 공급 기동에 실패했습니다.")
            print("두 번째 공급 작업 assembly_2 완료")
        after = fleet.workers[args.robot].get_status()
        print(
            f"통합 경로 {args.work_count}사이클 완료: {after['pose']} / "
            f"알람={after['alarms'] or '없음'}"
        )
        return 0
    finally:
        fleet.close_all()


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
