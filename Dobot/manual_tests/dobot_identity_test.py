"""등록된 Dobot 4대의 고유 식별정보를 읽는 무동작 검사.

USB COM 포트가 바뀌었을 때 같은 로봇을 자동으로 찾으려면 포트 번호가 아닌 고유한
값이 필요하다. 이 프로그램은 SDK의 시리얼번호와 장치명이 로봇마다 구분되는지
확인한다. 이동과 엔드이펙터 명령은 사용하지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from robot_control.dobot_config import ConfigError, DEFAULT_CONFIG, ROBOT_NAMES, load_config
from robot_control.dobot_worker import DobotWorker


def parse_args() -> argparse.Namespace:
    """설정 파일 위치와 포트별 제한시간을 실행 옵션으로 받는다."""
    parser = argparse.ArgumentParser(description="Dobot 고유 식별정보를 읽습니다.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--timeout", type=float, default=8.0)
    return parser.parse_args()


def main() -> int:
    """각 Dobot에 연결해 식별정보를 읽고 중복 여부를 검사한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if args.timeout <= 0:
        print("오류: --timeout 값은 0보다 커야 합니다.", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config, require_all_ports=True)
    except (ConfigError, OSError) as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    identities: dict[str, dict[str, str]] = {}
    failed = False
    for name in ROBOT_NAMES:
        item = config["robots"][name]
        worker = DobotWorker(name, item["port"], int(item["baudrate"]))
        try:
            worker.start(timeout=args.timeout)
            identity = worker.get_identity(timeout=args.timeout)
            identities[name] = identity
            print(
                f"{name}: {identity['port']} / "
                f"SN={identity['serial_number'] or '(없음)'} / "
                f"이름={identity['device_name'] or '(없음)'}"
            )
        except Exception as exc:
            failed = True
            print(f"{name}: 식별정보 읽기 실패 - {exc}")
        finally:
            worker.close()

    # 빈 시리얼번호는 자동 식별에 사용할 수 없으므로 제외한다.
    serial_numbers = [
        identity["serial_number"]
        for identity in identities.values()
        if identity["serial_number"]
    ]
    if len(serial_numbers) != len(ROBOT_NAMES):
        missing = [
            name
            for name in ROBOT_NAMES
            if not identities.get(name, {}).get("serial_number")
        ]
        print(
            "판정: 다음 로봇의 시리얼번호를 얻지 못했습니다: "
            + ", ".join(missing)
        )
        return 1
    if len(set(serial_numbers)) != len(serial_numbers):
        print("판정: 시리얼번호가 중복되어 자동 포트 복구에 사용할 수 없습니다.")
        return 1
    if failed:
        return 1

    print("판정: 네 시리얼번호가 모두 고유하므로 자동 포트 복구에 사용할 수 있습니다.")
    return 0


if __name__ == "__main__":
    # DobotWorker가 Windows 자식 프로세스를 사용하므로 필요한 시작 처리다.
    import multiprocessing as mp

    mp.freeze_support()
    raise SystemExit(main())
