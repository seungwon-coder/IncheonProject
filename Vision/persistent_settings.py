"""버전 폴더가 바뀌어도 유지되는 색상 기준과 검사영역 저장소."""
import json
import os
from pathlib import Path


def settings_path(root: Path) -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "PV5_VISION_CONTROL" / "user_settings.json") if base else (root / "user_data" / "user_settings.json")


def load_persistent_settings(config: dict, root: Path) -> Path:
    path = settings_path(root)
    if not path.is_file():
        return path
    saved = json.loads(path.read_text(encoding="utf-8-sig"))
    by_id = {int(station["id"]): station for station in config.get("stations", [])}
    for raw_id, values in saved.get("stations", {}).items():
        station = by_id.get(int(raw_id))
        if station is None:
            continue
        if isinstance(values.get("seat_color_calibration"), dict):
            station["seat_color_calibration"] = values["seat_color_calibration"]
        if isinstance(values.get("inspection_roi"), list) and len(values["inspection_roi"]) == 4:
            station["inspection_roi"] = values["inspection_roi"]
        polygon = values.get("inspection_roi_polygon")
        if isinstance(polygon, list) and len(polygon) >= 3:
            station["inspection_roi_polygon"] = polygon
        elif "inspection_roi_polygon" in values:
            station.pop("inspection_roi_polygon", None)
        if "roi_min_overlap" in values:
            station["roi_min_overlap"] = values["roi_min_overlap"]
        if isinstance(values.get("object_rois"), dict):
            station["object_rois"] = values["object_rois"]
        if "object_roi_min_overlap" in values:
            station["object_roi_min_overlap"] = values["object_roi_min_overlap"]
        if "show_object_rois" in values:
            station["show_object_rois"] = bool(values["show_object_rois"])
        if isinstance(values.get("v3_color_presence"), dict):
            station["v3_color_presence"] = values["v3_color_presence"]
    return path


def save_persistent_settings(config: dict, root: Path) -> Path:
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"stations": {}}
    for station in config.get("stations", []):
        values = {
            "inspection_roi": station.get("inspection_roi", [0.0, 0.0, 1.0, 1.0]),
            "inspection_roi_polygon": station.get("inspection_roi_polygon"),
            "roi_min_overlap": station.get("roi_min_overlap", 0.70),
            "object_rois": station.get("object_rois", {}),
            "object_roi_min_overlap": station.get("object_roi_min_overlap", 0.25),
            "show_object_rois": station.get("show_object_rois", False),
        }
        calibration = station.get("seat_color_calibration")
        if isinstance(calibration, dict):
            values["seat_color_calibration"] = calibration
        color_presence = station.get("v3_color_presence")
        if isinstance(color_presence, dict):
            values["v3_color_presence"] = color_presence
        data["stations"][str(station["id"])] = values
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path
