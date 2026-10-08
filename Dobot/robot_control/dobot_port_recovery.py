"""Dobot 시리얼번호를 이용해 변경된 COM 포트를 자동 복구한다.

최초 등록 시 저장한 시리얼번호는 로봇 전원이나 PC를 다시 켜도 바뀌지 않는다.
이 프로그램은 현재 연결된 포트를 검색하고 각 장치의 시리얼번호를 읽어, 기존의
Dobot_1~4와 일치하는 현재 COM 포트를 찾아 robots.json을 갱신한다.

안전 원칙:
- 이동, HOME, JOG, 그리퍼, 흡착컵 명령을 사용하지 않는다.
- 네 로봇을 모두 찾았을 때만 설정 파일을 한 번에 변경한다.
- 일부만 찾으면 기존 설정을 그대로 보존한다.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
from pathlib import Path
from typing import Any

from robot_control.dobot_config import ConfigError, DEFAULT_CONFIG, ROBOT_NAMES, load_config, save_config
from manual_tests.dobot_connection_test import (
    DEFAULT_SDK_DIR,
    load_sdk,
    normalize_ports,
)
from robot_control.dobot_worker import DobotWorker


def discover_ports(sdk_dir: Path) -> list[str]:
    """제조사 SDK로 현재 PC에서 보이는 Dobot 후보 포트를 검색한다."""
    d_type = load_sdk(sdk_dir)
    api = d_type.load()
    return normalize_ports(d_type.SearchDobot(api))


def match_ports_by_serial(
    config: dict[str, Any],
    identities_by_port: dict[str, str],
) -> dict[str, str]:
    """저장된 시리얼번호와 현재 검색 결과를 비교해 새 포트 매핑을 만든다."""
    serial_to_port = {
        serial_number: port
        for port, serial_number in identities_by_port.items()
        if serial_number
    }
    recovered: dict[str, str] = {}
    for robot_name in ROBOT_NAMES:
        serial_number = config["robots"][robot_name].get("serial_number")
        if not serial_number:
            raise ConfigError(f"{robot_name}의 시리얼번호가 등록되지 않았습니다.")
        if serial_number not in serial_to_port:
            raise ConfigError(
                f"{robot_name} 장치를 찾지 못했습니다. 저장된 SN={serial_number}"
            )
        recovered[robot_name] = serial_to_port[serial_number]
    return recovered


def parse_args() -> argparse.Namespace:
    """설정·SDK 경로와 포트별 제한시간을 실행 옵션으로 받는다."""
    parser = argparse.ArgumentParser(description="Dobot COM 포트를 자동 복구합니다.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--sdk-dir", type=Path, default=DEFAULT_SDK_DIR)
    parser.add_argument("--timeout", type=float, default=8.0)
    return parser.parse_args()


def main() -> int:
    """포트 검색 → SN 읽기 → 4대 매칭 → 설정 저장 순서로 실행한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if args.timeout <= 0:
        print("오류: --timeout 값은 0보다 커야 합니다.", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
        ports = discover_ports(args.sdk_dir.resolve())
        print(f"검색된 후보 포트: {', '.join(ports) if ports else '없음'}")

        identities_by_port: dict[str, str] = {}
        for index, port in enumerate(ports, start=1):
            # 임시 이름은 검색 순서 표시용이며 실제 Dobot 번호를 뜻하지 않는다.
            worker = DobotWorker(f"후보_{index}", port, sdk_dir=args.sdk_dir.resolve())
            try:
                worker.start(timeout=args.timeout)
                identity = worker.get_identity(timeout=args.timeout)
                serial_number = identity["serial_number"]
                if serial_number:
                    identities_by_port[port] = serial_number
                    print(f"{port}: Dobot SN={serial_number}")
            except Exception as exc:
                print(f"{port}: 대상 아님 또는 응답 실패 - {exc}")
            finally:
                worker.close()

        # 네 대가 모두 매칭된 뒤에만 메모리의 설정을 변경한다.
        recovered = match_ports_by_serial(config, identities_by_port)
        for robot_name, current_port in recovered.items():
            old_port = config["robots"][robot_name].get("port")
            config["robots"][robot_name]["port"] = current_port
            print(f"{robot_name}: {old_port or '미등록'} → {current_port}")

        # save_config는 임시 파일 작성 후 교체하므로 저장 도중 손상 위험도 줄인다.
        save_config(config, args.config)
        print("4대 COM 포트 자동 복구 및 설정 저장 완료")
        return 0
    except (ConfigError, OSError) as exc:
        print(f"자동 복구 실패: {exc}", file=sys.stderr)
        print("기존 robots.json은 변경하지 않았습니다.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
