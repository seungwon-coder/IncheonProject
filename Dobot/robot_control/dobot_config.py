"""Dobot 4대의 COM 포트 설정을 안전하게 관리하는 프로그램.

COM 포트는 USB를 다시 연결하면 바뀔 수 있다. 이 파일은 사용자가 확인한 포트를
Dobot_1~4에 등록하고, 같은 포트를 두 로봇에 잘못 등록하는 실수를 검사한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


# 프로그램 전체에서 공통으로 사용하는 경로와 허용값이다.
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BASE_DIR / "config" / "robots.json"
ROBOT_NAMES = tuple(f"Dobot_{number}" for number in range(1, 5))
PORT_PATTERN = re.compile(r"^COM([1-9][0-9]*)$", re.IGNORECASE)


class ConfigError(ValueError):
    """robots.json 내용이 잘못됐을 때 사용하는 전용 오류."""

    pass


def normalize_port(value: str | None) -> str | None:
    """'com14' 같은 입력을 표준 형태인 'COM14'로 바꾸고 형식을 검사한다."""
    if value is None:
        return None
    port = value.strip().upper()
    if not PORT_PATTERN.fullmatch(port):
        raise ConfigError(f"올바르지 않은 COM 포트입니다: {value!r}")
    return port


def validate_config(data: dict[str, Any], require_all_ports: bool = False) -> dict[str, Any]:
    """설정 파일에 필요한 로봇과 항목이 모두 올바른지 확인한다."""
    if data.get("schema_version") != 1:
        raise ConfigError("지원하지 않는 설정 파일 버전입니다.")
    robots = data.get("robots")
    if not isinstance(robots, dict) or set(robots) != set(ROBOT_NAMES):
        raise ConfigError("robots에는 Dobot_1부터 Dobot_4까지 정확히 있어야 합니다.")

    # 이미 사용한 포트를 기억해 두었다가 중복 등록을 찾아낸다.
    used_ports: dict[str, str] = {}
    used_serial_numbers: dict[str, str] = {}
    for name in ROBOT_NAMES:
        item = robots[name]
        if not isinstance(item, dict):
            raise ConfigError(f"{name} 설정이 객체가 아닙니다.")
        port = normalize_port(item.get("port"))
        item["port"] = port
        if require_all_ports and port is None:
            raise ConfigError(f"{name}의 COM 포트가 등록되지 않았습니다.")
        if port is not None:
            if port in used_ports:
                raise ConfigError(f"{port}가 {used_ports[port]}와 {name}에 중복 등록됐습니다.")
            used_ports[port] = name
        serial_number = item.get("serial_number")
        if serial_number is not None:
            if not isinstance(serial_number, str) or not serial_number.strip():
                raise ConfigError(f"{name}의 serial_number가 올바르지 않습니다.")
            serial_number = serial_number.strip()
            item["serial_number"] = serial_number
            if serial_number in used_serial_numbers:
                raise ConfigError(
                    f"시리얼번호 {serial_number}가 {used_serial_numbers[serial_number]}와 "
                    f"{name}에 중복 등록됐습니다."
                )
            used_serial_numbers[serial_number] = name
        if item.get("end_effector") not in {"gripper", "suction"}:
            raise ConfigError(f"{name}의 엔드이펙터 설정이 올바르지 않습니다.")
        if not isinstance(item.get("teaching_points"), list):
            raise ConfigError(f"{name}의 teaching_points가 목록이 아닙니다.")
    return data


def load_config(path: Path = DEFAULT_CONFIG, require_all_ports: bool = False) -> dict[str, Any]:
    """JSON 설정 파일을 읽은 뒤 검증된 설정만 반환한다."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"설정 파일이 없습니다: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON 형식 오류: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("설정 파일의 최상위 값은 객체여야 합니다.")
    return validate_config(data, require_all_ports=require_all_ports)


def save_config(data: dict[str, Any], path: Path = DEFAULT_CONFIG) -> None:
    """검증된 설정을 임시 파일에 먼저 쓴 뒤 원본과 교체한다.

    저장 도중 문제가 생겨도 기존 설정이 반쯤 기록되는 위험을 줄이는 방식이다.
    """
    validated = validate_config(deepcopy(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def assign_port(robot_name: str, port: str | None, path: Path = DEFAULT_CONFIG) -> None:
    """로봇 하나에 포트를 등록한다. port가 None이면 등록을 해제한다."""
    if robot_name not in ROBOT_NAMES:
        raise ConfigError(f"로봇 이름은 {', '.join(ROBOT_NAMES)} 중 하나여야 합니다.")
    data = load_config(path)
    data["robots"][robot_name]["port"] = normalize_port(port)
    save_config(data, path)


def print_config(data: dict[str, Any]) -> None:
    """현재 로봇별 포트와 설정을 표 형태로 출력한다."""
    print("로봇 등록 상태")
    print("-" * 58)
    for name in ROBOT_NAMES:
        item = data["robots"][name]
        port = item["port"] or "미등록"
        serial_number = item.get("serial_number") or "미등록"
        print(
            f"{name:<10} {port:<10} {item['end_effector']:<9} "
            f"SN={serial_number:<14} 포인트 {len(item['teaching_points'])}개"
        )


def parse_args() -> argparse.Namespace:
    """--assign과 --clear 명령행 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description="Dobot 번호와 COM 포트를 등록합니다.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--assign", nargs=2, metavar=("DOBOT", "PORT"))
    actions.add_argument("--clear", metavar="DOBOT")
    return parser.parse_args()


def main() -> int:
    """포트 등록·해제 요청을 처리하고 최종 설정을 보여준다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        if args.assign:
            assign_port(args.assign[0], args.assign[1], args.config)
        elif args.clear:
            assign_port(args.clear, None, args.config)
        print_config(load_config(args.config))
        return 0
    except (ConfigError, OSError) as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
