import json
import os
import tempfile
from pathlib import Path

from persistent_settings import load_persistent_settings, save_persistent_settings


with tempfile.TemporaryDirectory() as folder:
    previous = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = folder
    try:
        source = {
            "stations": [
                {"id": 2, "seat_color_calibration": {
                    "cocoa_lab": [130.0, 136.0, 111.0],
                    "dark_lab": [90.0, 132.0, 120.0],
                    "min_separation": 0.1,
                    "max_distance": 55.0,
                }}
            ]
        }
        path = save_persistent_settings(source, Path(folder))
        assert path.is_file()
        reloaded = {"stations": [{"id": 2, "seat_color_calibration": {
            "cocoa_lab": None, "dark_lab": None, "min_separation": 0.1, "max_distance": 55.0}}]}
        load_persistent_settings(reloaded, Path(folder))
        calibration = reloaded["stations"][0]["seat_color_calibration"]
        assert calibration["cocoa_lab"] == [130.0, 136.0, 111.0]
        assert calibration["dark_lab"] == [90.0, 132.0, 120.0]
        json.loads(path.read_text(encoding="utf-8"))
    finally:
        if previous is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous

print("PASS: LAB 기준색을 Windows 사용자 설정에 저장하고 재시작 시 복원")
