import numpy as np
import vision_core_server as core


station = {
    "seat_color_calibration": {
        "cocoa_lab": [110, 140, 150],
        "dark_lab": [55, 138, 145],
        "min_separation": 0.10,
        "max_distance": 55.0,
        "sample_method": 2,
    }
}

original = core._seat_lab_pixels
try:
    core._seat_lab_pixels = lambda frame, box, station=None: np.tile([108, 141, 149], (100, 1)).astype(float)
    assert core.classify_seat_color(station, None, [0, 0, 1, 1])[0] == "cocoa"
    core._seat_lab_pixels = lambda frame, box, station=None: np.tile([57, 137, 146], (100, 1)).astype(float)
    assert core.classify_seat_color(station, None, [0, 0, 1, 1])[0] == "dark"
    core._seat_lab_pixels = lambda frame, box, station=None: np.tile([82, 139, 148], (100, 1)).astype(float)
    assert core.classify_seat_color(station, None, [0, 0, 1, 1])[0] in ("cocoa", "dark")
finally:
    core._seat_lab_pixels = original

# 은색/반사 이상치가 섞여도 시트색 다수 픽셀로 판정합니다.
pixels = np.vstack((np.tile([108, 141, 149], (70, 1)), np.tile([190, 128, 128], (30, 1)))).astype(float)
assert core._robust_reference_distance(pixels, [110, 140, 150], .60) < core._robust_reference_distance(pixels, [55, 138, 145], .60)

# 두 기준색이 준비된 뒤에는 거리 임계값 때문에 미분류하지 않습니다.
strict_station = {"seat_color_force_nearest": True, "seat_color_calibration": {
    "cocoa_lab": [110, 140, 150], "dark_lab": [55, 138, 145],
    "min_separation": .99, "max_distance": .01, "sample_method": 2}}
original = core._seat_lab_pixels
try:
    core._seat_lab_pixels = lambda frame, box, station=None: np.tile([105, 141, 149], (100, 1)).astype(float)
    assert core.classify_seat_color(strict_station, None, [0, 0, 1, 1])[0] == "cocoa"
finally:
    core._seat_lab_pixels = original

d = core.Detection("car body", "body", 0.2, [0, 0, 1, 1], None)
assert core.decide({"id": 4, "inspection": "sort"}, [], 0, 0.8) is None
assert core.decide({"id": 4, "inspection": "sort"}, [d], 0, 0.8) is None
print("PASS: OpenCV LAB color decision and empty-jig waits for ERROR timeout")
