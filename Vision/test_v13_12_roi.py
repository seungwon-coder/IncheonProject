import asyncio
import json
import os
import tempfile
from pathlib import Path

import numpy as np

import vision_core_server as core
from persistent_settings import load_persistent_settings, save_persistent_settings


frame = np.zeros((480, 640, 3), dtype=np.uint8)
station = {"id": 1, "inspection_roi": [0.25, 0.25, 0.75, 0.75], "roi_min_overlap": 0.70}
inside = core.Detection("part", "round", 0.99, [200, 150, 300, 250], None)
outside = core.Detection("part", "round", 0.99, [0, 0, 100, 100], None)
boundary = core.Detection("part", "round", 0.99, [130, 100, 230, 200], None)
assert core.roi_pixels(station, frame) == (160, 120, 480, 360)
assert core.filter_detections_by_roi(station, frame, [inside, outside]) == [inside]
assert core.filter_detections_by_roi(station, frame, [boundary]) == []

# 마름모 ROI: 중앙 검출은 포함하고 좌측 상단 검출은 제외합니다.
diamond_station = {
    "id": 1,
    "inspection_roi_polygon": [[0.50, 0.10], [0.90, 0.50], [0.50, 0.90], [0.10, 0.50]],
    "roi_min_overlap": 0.70,
}
center = core.Detection("part", "round", 0.99, [280, 200, 360, 280], None)
corner = core.Detection("part", "round", 0.99, [0, 0, 80, 80], None)
assert core.filter_detections_by_roi(diamond_station, frame, [center, corner]) == [center]

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    os.environ["LOCALAPPDATA"] = folder
    config = {"stations": [station.copy()]}
    config["stations"][0]["inspection_roi_polygon"] = diamond_station["inspection_roi_polygon"]
    save_persistent_settings(config, root)
    restored = {"stations": [{"id": 1}]}
    load_persistent_settings(restored, root)
    assert restored["stations"][0]["inspection_roi"] == station["inspection_roi"]
    assert restored["stations"][0]["inspection_roi_polygon"] == diamond_station["inspection_roi_polygon"]
    assert restored["stations"][0]["roi_min_overlap"] == 0.70

print("PASS: rectangle/polygon ROI filters detections and persists across restarts")
