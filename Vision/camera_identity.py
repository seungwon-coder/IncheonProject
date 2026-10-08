"""Windows 카메라 장치 경로를 현재 OpenCV 번호로 변환하는 공통 모듈."""
from __future__ import annotations

import re
from typing import Any, Iterable


def _key(value: Any) -> str:
    """DirectShow 경로와 PnP InstanceId를 비교 가능한 USB 핵심값으로 정규화합니다."""
    text = str(value or "").strip().casefold().replace("\\\\?\\", "")
    text = text.replace("\\global", "").replace("/", "\\")
    # DirectShow: usb#vid...#instance#{guid}, PnP: USB\VID...\instance
    text = text.replace("#", "\\")
    parts = [p for p in text.split("\\") if p]
    usb_at = next((i for i, p in enumerate(parts) if p == "usb"), None)
    if usb_at is not None and len(parts) >= usb_at + 3:
        return "\\".join(parts[usb_at:usb_at + 3])
    return re.sub(r"\s+", "", text)


def camera_records(backend: str = "DSHOW"):
    """카메라를 열지 않고 해당 백엔드의 현재 번호/장치 경로를 조회합니다."""
    import cv2
    from cv2_enumerate_cameras import enumerate_cameras

    api = getattr(cv2, "CAP_" + str(backend).upper())
    return list(enumerate_cameras(api))


def resolve_camera(station: dict[str, Any], records: Iterable[Any] | None = None):
    """저장된 camera_path/instance_id를 현재 OpenCV 번호와 백엔드로 변환합니다."""
    import cv2

    backend_name = str(station.get("backend", "DSHOW")).upper()
    backend = getattr(cv2, "CAP_" + backend_name)
    requested = str(station.get("camera_path") or station.get("instance_id") or "").strip()
    if not requested:
        return int(station["camera"]), backend, None

    devices = list(records) if records is not None else camera_records(backend_name)
    wanted = _key(requested)
    matches = [device for device in devices if _key(getattr(device, "path", "")) == wanted]
    if len(matches) == 1:
        device = matches[0]
        return int(device.index), int(device.backend), device

    visible = "\n".join(
        f"번호 {d.index}: {d.name} | {d.path}" for d in devices
    ) or "현재 조회된 카메라 없음"
    if not matches:
        raise RuntimeError(
            "지정한 카메라 인스턴스/장치 경로를 현재 장치 목록에서 찾지 못했습니다.\n"
            f"요청: {requested}\n{visible}"
        )
    raise RuntimeError(f"같은 카메라 식별값이 {len(matches)}개 조회되었습니다.\n{visible}")


def resolve_all(stations: Iterable[dict[str, Any]]) -> dict[int, tuple[int, int, Any]]:
    """실행 시작 시 모든 비전 카메라를 한 번의 장치 목록으로 안정적으로 매핑합니다."""
    stations = list(stations)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for station in stations:
        grouped.setdefault(str(station.get("backend", "DSHOW")).upper(), []).append(station)

    resolved: dict[int, tuple[int, int, Any]] = {}
    used: dict[tuple[int, int], int] = {}
    for backend_name, items in grouped.items():
        records = camera_records(backend_name)
        for station in items:
            value = resolve_camera(station, records)
            key = (value[0], value[1])
            if key in used:
                raise RuntimeError(
                    f"비전{station['id']}과 비전{used[key]}이 같은 카메라 번호 {value[0]}에 연결됩니다."
                )
            used[key] = int(station["id"])
            resolved[int(station["id"])] = value
    return resolved
