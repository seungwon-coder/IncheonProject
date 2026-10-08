"""Dobot 4대의 티칭 좌표를 JSON 파일로 안전하게 관리한다.

로봇 구성 파일에는 필요한 포인트의 이름만 있고, 이 파일은 각 포인트의 실제
X/Y/Z/R 좌표와 속도, 수정 시각, 유효 여부를 별도 파일에 저장한다.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from robot_control.dobot_config import DEFAULT_CONFIG, ROBOT_NAMES, load_config


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TEACHING_FILE = BASE_DIR / "config" / "teaching_points.json"
POSE_AXES = ("x", "y", "z", "r")


class TeachingPointError(ValueError):
    """티칭 포인트 이름이나 저장 데이터가 잘못됐을 때 발생하는 오류."""


def new_teaching_data(robot_config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """robots.json의 필수 포인트 이름으로 비어 있는 티칭 데이터를 만든다."""
    config = load_config(robot_config_path)
    robots: dict[str, Any] = {}
    for robot_name in ROBOT_NAMES:
        point_names = config["robots"][robot_name]["teaching_points"]
        robots[robot_name] = {
            point_name: {
                "pose": None,
                "speed_percent": 5.0,
                "updated_at": None,
                "valid": False,
            }
            for point_name in point_names
        }
    return {"schema_version": 1, "robots": robots}


def validate_teaching_data(
    data: dict[str, Any], robot_config_path: Path = DEFAULT_CONFIG
) -> dict[str, Any]:
    """필수 포인트 누락과 좌표·속도 형식을 검사한다."""
    expected = new_teaching_data(robot_config_path)
    if data.get("schema_version") != 1:
        raise TeachingPointError("지원하지 않는 티칭 파일 버전입니다.")
    robots = data.get("robots")
    if not isinstance(robots, dict) or set(robots) != set(ROBOT_NAMES):
        raise TeachingPointError("티칭 파일에는 Dobot_1~4가 정확히 있어야 합니다.")

    for robot_name in ROBOT_NAMES:
        points = robots[robot_name]
        expected_names = set(expected["robots"][robot_name])
        if not isinstance(points, dict) or set(points) != expected_names:
            raise TeachingPointError(f"{robot_name}의 필수 티칭 포인트 구성이 다릅니다.")
        for point_name, point in points.items():
            if not isinstance(point, dict) or not isinstance(point.get("valid"), bool):
                raise TeachingPointError(f"{robot_name}/{point_name} 형식 오류")
            speed = point.get("speed_percent")
            if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not 1 <= speed <= 100:
                raise TeachingPointError(f"{robot_name}/{point_name} 속도는 1~100%여야 합니다.")
            pose = point.get("pose")
            updated_at = point.get("updated_at")
            if point["valid"]:
                if not isinstance(pose, dict) or set(pose) != set(POSE_AXES):
                    raise TeachingPointError(f"{robot_name}/{point_name} 좌표 형식 오류")
                for axis in POSE_AXES:
                    value = pose[axis]
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        raise TeachingPointError(f"{robot_name}/{point_name}/{axis}는 숫자여야 합니다.")
                if not isinstance(updated_at, str) or not updated_at:
                    raise TeachingPointError(f"{robot_name}/{point_name} 수정 시각이 없습니다.")
            elif pose is not None or updated_at is not None:
                raise TeachingPointError(
                    f"{robot_name}/{point_name} 미등록 포인트에는 좌표·시각이 없어야 합니다."
                )
    return data


def load_teaching_data(
    path: Path = DEFAULT_TEACHING_FILE,
    robot_config_path: Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """티칭 JSON을 읽고 검사가 끝난 데이터만 반환한다."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TeachingPointError(f"티칭 파일이 없습니다: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TeachingPointError(f"티칭 JSON 형식 오류: {exc}") from exc
    if not isinstance(data, dict):
        raise TeachingPointError("티칭 파일 최상위 값은 객체여야 합니다.")
    return validate_teaching_data(data, robot_config_path)


def save_teaching_data(
    data: dict[str, Any],
    path: Path = DEFAULT_TEACHING_FILE,
    robot_config_path: Path = DEFAULT_CONFIG,
) -> None:
    """검증 후 임시 파일을 원본과 교체해 중간 손상을 방지한다."""
    validated = validate_teaching_data(deepcopy(data), robot_config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def teach_point(
    data: dict[str, Any],
    robot_name: str,
    point_name: str,
    pose: dict[str, float],
    speed_percent: float = 5.0,
) -> dict[str, Any]:
    """현재 좌표를 지정 포인트에 기록하고 유효 상태로 바꾼다."""
    copied = deepcopy(data)
    try:
        point = copied["robots"][robot_name][point_name]
    except KeyError as exc:
        raise TeachingPointError(f"등록되지 않은 포인트: {robot_name}/{point_name}") from exc
    if set(pose) != set(POSE_AXES):
        raise TeachingPointError("좌표에는 x, y, z, r이 정확히 있어야 합니다.")
    point.update(
        pose={axis: float(pose[axis]) for axis in POSE_AXES},
        speed_percent=float(speed_percent),
        updated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        valid=True,
    )
    return validate_teaching_data(copied)


def missing_points(data: dict[str, Any], robot_name: str | None = None) -> list[str]:
    """좌표가 아직 등록되지 않은 필수 포인트의 전체 이름을 반환한다."""
    names = (robot_name,) if robot_name else ROBOT_NAMES
    missing: list[str] = []
    for name in names:
        if name not in ROBOT_NAMES:
            raise TeachingPointError(f"등록되지 않은 로봇: {name}")
        for point_name, point in data["robots"][name].items():
            if not point["valid"]:
                missing.append(f"{name}/{point_name}")
    return missing
