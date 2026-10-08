"""
PV5 V13.29 Vision3 Color Calibration Coordinate Fix Core

특징
1. 카메라를 각 1회만 열어 AI 검사, 로컬 미리보기, SCADA 송출에 함께 사용
2. YOLO detect/segment 자동 판별: 사각 바운딩박스 또는 부품 외곽선 표시
3. 주문번호, 예상 부품, 실제 검출, 신뢰도, OK/NG/ERROR를 영상에 표시
4. /vision1, /vision2, /vision3, /vision4, / 주소 제공
5. 제어판과 SCADA는 이 서버의 API/MJPEG만 사용하므로 카메라 중복 점유 없음
6. 프레임 끊김 시 카메라 해제, 재검색, 순차 재연결

실행 예
    python vision_core_server.py --config config.json --preview
    python vision_core_server.py --config config.json --write --preview
    python vision_core_server.py --config config.json --no-opc --demo-product 1 --preview

주의
    V13 control_panel.py는 카메라를 열지 않으므로 동시에 실행할 수 있습니다.
    Core 서버 자체는 중복 실행할 수 없습니다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import threading
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from camera_identity import camera_records, resolve_camera
from persistent_settings import load_persistent_settings, save_persistent_settings


ROOT = Path(__file__).resolve().parent
CAMERA_OPEN_LOCK = threading.Lock()


ORDER_MAP = {
    1: {"car": "VAN",   "seat": "COCOA", "lamp": "ROUND", "seat_count": 2},
    2: {"car": "VAN",   "seat": "COCOA", "lamp": "EDGE",  "seat_count": 2},
    3: {"car": "VAN",   "seat": "DARK",  "lamp": "ROUND", "seat_count": 2},
    4: {"car": "VAN",   "seat": "DARK",  "lamp": "EDGE",  "seat_count": 2},
    5: {"car": "TRUCK", "seat": "COCOA", "lamp": "ROUND", "seat_count": 1},
    6: {"car": "TRUCK", "seat": "COCOA", "lamp": "EDGE",  "seat_count": 1},
    7: {"car": "TRUCK", "seat": "DARK",  "lamp": "ROUND", "seat_count": 1},
    8: {"car": "TRUCK", "seat": "DARK",  "lamp": "EDGE",  "seat_count": 1},
}


# 여러 사람이 만든 모델에서 클래스명이 조금 달라도 같은 의미로 처리합니다.
ALIASES = {
    "seat_color_01": "cocoa",
    "seat_color_02": "dark",
    "seat_cocoa": "cocoa",
    "seat_dark": "dark",
    "seat_dark_brown": "dark",
    "Gloss/Standard": "cocoa",
    "Matte Brown": "dark",
    "car seat": "seat_unknown",
    "lamp_edge_01": "edge",
    "lamp_round_02": "round",
    "lamp_edge": "edge",
    "lamp_round": "round",
    "car lamp_A": "edge",
    "car lamp_R": "round",
    # 비전3은 램프 형상이 아니라 실제 점등 상태를 검사합니다.
    "lamp_on": "lamp_on",
    "lamp_light_on": "lamp_on",
    "lamp_led_on": "lamp_on",
    "car body": "body",
    "empty_jig": "empty",
    "empty jig": "empty",
}


V4_CODE = {
    "empty": 1,
    "round": 2,
    "edge": 3,
    "cocoa": 4,
    "dark": 5,
    "body": 6,
}


COLORS = {
    "cocoa": (45, 105, 170),
    "dark": (55, 45, 40),
    "round": (55, 210, 70),
    "edge": (0, 190, 255),
    "lamp_on": (0, 255, 255),
    "lamp_candidate": (0, 190, 255),
    "lamp_off": (90, 90, 90),
    "body": (255, 140, 40),
    "empty": (170, 170, 170),
    "seat_unknown": (230, 90, 230),
    "unknown": (0, 0, 255),
}


KOREAN_VALUE = {
    "cocoa": "코코아 브라운",
    "dark": "다크 브라운",
    "round": "둥근 램프",
    "edge": "각진 램프",
    "lamp_on": "램프 점등",
    "lamp_candidate": "램프",
    "lamp_off": "램프 미점등",
    "body": "차체",
    "empty": "빈 지그",
    "seat_unknown": "시트 색상 미분류",
    "none": "미검출",
    "-": "-",
}


def order_parts(code: int) -> tuple[str, str, int]:
    if type(code) is not int or code not in ORDER_MAP:
        raise ValueError("주문번호는 1~8이어야 합니다. 0은 생산 없음입니다.")
    data = ORDER_MAP[code]
    return data["seat"].lower(), data["lamp"].lower(), data["seat_count"]


def inspection_role(station: dict[str, Any]) -> str:
    default = {1: "lamp", 2: "seat", 3: "assembly", 4: "sort"}
    role = station.get("inspection", default.get(station["id"]))
    if role not in ("seat", "lamp", "assembly", "sort"):
        raise ValueError(f"지원하지 않는 검사 역할: {role}")
    return role


def class_mapping(station: dict[str, Any]) -> dict[str, str]:
    mapping = dict(ALIASES)
    # config.json의 classes 설정을 최우선으로 적용합니다.
    for name, value in station.get("classes", {}).items():
        mapping[name] = str(value).lower()
    return mapping


def mapped_value(station: dict[str, Any], class_name: str) -> str:
    return class_mapping(station).get(class_name, "unknown")


def relevant_values(role: str) -> set[str]:
    return {
        "seat": {"cocoa", "dark", "seat_unknown"},
        "lamp": {"round", "edge"},
        "assembly": {"cocoa", "dark", "seat_unknown", "lamp_on", "lamp_off"},
        "sort": {"empty", "cocoa", "dark", "round", "edge", "body", "seat_unknown"},
    }[role]


def required_model_values(role: str) -> set[str]:
    """AI 모델은 위치/종류만 검출하고 색상과 점등은 OpenCV가 판정합니다."""
    return {
        "lamp": {"round", "edge"},
        "seat": {"seat_unknown"},
        # 비전3은 시트 유무와 LED를 모두 OpenCV로 검사하므로 AI 클래스가 필요 없습니다.
        "assembly": set(),
        "sort": {"round", "edge", "seat_unknown", "body"},
    }[role]


def validate_vision_model(station: dict[str, Any], model: Any) -> None:
    """Detection과 Segment 모델을 모두 허용합니다.

    Segment 모델은 폴리곤 외곽선을, Detection 모델은 바운딩박스를 표시합니다.
    판정 로직은 두 모델 모두 동일한 클래스명과 신뢰도를 사용합니다.
    """
    if model.task not in ("detect", "segment"):
        raise ValueError(
            f"V{station['id']} 모델 작업 유형이 '{model.task}'입니다. "
            "YOLO detect 또는 segment best.pt가 필요합니다."
        )

    role = inspection_role(station)
    values = {mapped_value(station, name) for name in model.names.values()}
    missing = required_model_values(role) - values
    if missing:
        raise ValueError(
            f"V{station['id']} 모델 필수 클래스 부족: {sorted(missing)}. "
            f"현재 모델 클래스={list(model.names.values())}"
        )


@dataclass
class Detection:
    name: str
    value: str
    score: float
    box: list[float]
    polygon: Any


def predict_result_detections(model, station, image, offset=(0, 0), region_key=None):
    """한 이미지의 YOLO 결과를 원본 프레임 좌표 Detection 목록으로 변환합니다."""
    import numpy as np
    result=model.predict(image,conf=float(station.get("display_confidence",.25)),
        iou=float(station.get("nms_iou",.70)),imgsz=int(station.get("imgsz",640)),
        device=station.get("device","cpu"),verbose=False)[0]
    output=[]
    if result.boxes is None:return output
    classes=result.boxes.cls.cpu().tolist();scores=result.boxes.conf.cpu().tolist()
    boxes=result.boxes.xyxy.cpu().tolist();polygons=result.masks.xy if result.masks is not None else []
    ox,oy=offset
    for index in range(min(len(classes),len(scores),len(boxes))):
        class_name=model.names[int(classes[index])];value=mapped_value(station,class_name)
        box=[boxes[index][0]+ox,boxes[index][1]+oy,boxes[index][2]+ox,boxes[index][3]+oy]
        polygon=None
        if index<len(polygons):
            polygon=np.asarray(polygons[index],dtype=float).copy();polygon[:,0]+=ox;polygon[:,1]+=oy
        name=f"{class_name} [{region_key}]" if region_key else class_name
        output.append(Detection(name,value,float(scores[index]),box,polygon))
    return output


def point_in_polygon(x: float, y: float, polygon) -> bool:
    """외부 패키지 없이 점이 다각형 내부에 있는지 확인합니다."""
    points=list(polygon)
    if len(points)<3:return False
    inside=False
    previous=points[-1]
    for current in points:
        x1,y1=previous;x2,y2=current
        # 수평선과 다각형 변의 교차 여부를 홀짝 규칙으로 계산합니다.
        if (y1>y)!=(y2>y):
            cross_x=(x2-x1)*(y-y1)/(y2-y1)+x1
            if x<cross_x:inside=not inside
        previous=current
    return inside


def predict_v3_seat_rois(model, station, frame):
    """시트1·시트2 ROI를 각각 잘라 독립 추론하고 자기 ROI 결과만 채택합니다.

    크롭 여유영역에 옆 시트가 보이더라도 검출 중심점과 검출영역 대부분이
    원래 ROI 안에 들어오지 않으면 해당 위치의 시트로 인정하지 않습니다.
    """
    import numpy as np
    seat_values={"cocoa","dark","seat_unknown"};output=[]
    for key in ("seat1","seat2"):
        points=object_roi_polygon_pixels(station,key,frame)
        if not points:continue
        polygon=np.asarray(points,dtype=np.int32)
        x=int(polygon[:,0].min());y=int(polygon[:,1].min())
        w=int(polygon[:,0].max()-x+1);h=int(polygon[:,1].max()-y+1)
        # ROI 경계에 닿은 시트가 잘리지 않도록 사방에 여유 공간을 추가합니다.
        padding=float(station.get("v3_roi_crop_padding",0.12))
        px,py=int(round(w*padding)),int(round(h*padding))
        frame_h,frame_w=frame.shape[:2]
        x1,y1=max(0,x-px),max(0,y-py);x2,y2=min(frame_w,x+w+px),min(frame_h,y+h+py)
        x,y,w,h=x1,y1,x2-x1,y2-y1
        if w<8 or h<8:continue
        crop=frame[y:y+h,x:x+w].copy()
        # 검은 마스크가 시트 외곽을 잘라 오검출시키는 문제를 피하기 위해
        # 다각형의 바깥도 유지한 사각 크롭을 AI에 입력합니다.
        # 기존 버전에서 저장된 낮은 겹침률 설정과 분리해 V13.27 전용 기준을 사용합니다.
        minimum=float(station.get("v3_seat_detection_min_overlap",0.60))
        candidates=[]
        for detection in predict_result_detections(model,station,crop,(x,y),key):
            if detection.value not in seat_values:
                continue
            center_x=(detection.box[0]+detection.box[2])/2.0
            center_y=(detection.box[1]+detection.box[3])/2.0
            center_inside=point_in_polygon(center_x,center_y,points)
            overlap=detection_roi_overlap(detection,points,frame.shape)
            if center_inside and overlap>=minimum:
                candidates.append(detection)
        if candidates:
            # 각 물리 위치에는 시트 한 개만 올 수 있으므로 가장 신뢰도 높은 하나만 사용합니다.
            output.append(max(candidates,key=lambda d:d.score))
    missing=[key for key in ("seat1","seat2") if not object_roi_polygon_pixels(station,key,frame)]
    for key in missing:
        output.append(Detection(f"{key} ROI 미설정","seat_roi_unconfigured",1.0,[0,0,0,0],None))
    return output


def roi_pixels(station: dict[str, Any], frame) -> tuple[int, int, int, int]:
    """정규화된 ROI [x1,y1,x2,y2]를 현재 프레임 좌표로 변환합니다."""
    height, width = frame.shape[:2]
    raw = station.get("inspection_roi", [0.0, 0.0, 1.0, 1.0])
    try:
        x1, y1, x2, y2 = [float(v) for v in raw]
    except (TypeError, ValueError):
        x1, y1, x2, y2 = 0.0, 0.0, 1.0, 1.0
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    return int(round(x1 * width)), int(round(y1 * height)), int(round(x2 * width)), int(round(y2 * height))


def roi_polygon_pixels(station: dict[str, Any], frame) -> list[tuple[int, int]]:
    """저장된 다각형 ROI를 픽셀 좌표로 변환하고, 없으면 사각형 ROI를 사용합니다."""
    height, width = frame.shape[:2]
    raw = station.get("inspection_roi_polygon")
    if isinstance(raw, list) and len(raw) >= 3:
        try:
            points = [
                (int(round(max(0.0, min(1.0, float(point[0]))) * (width - 1))),
                 int(round(max(0.0, min(1.0, float(point[1]))) * (height - 1))))
                for point in raw if isinstance(point, (list, tuple)) and len(point) == 2
            ]
            if len(points) >= 3:
                return points
        except (TypeError, ValueError):
            pass
    x1, y1, x2, y2 = roi_pixels(station, frame)
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def detection_roi_overlap(detection: Detection, roi, frame_shape) -> float:
    """검출 마스크(없으면 박스) 중 사각형/다각형 검사영역 안의 비율을 반환합니다."""
    import numpy as np
    from PIL import Image, ImageDraw

    height, width = frame_shape[:2]
    if isinstance(roi, tuple) and len(roi) == 4:
        rx1, ry1, rx2, ry2 = roi
        roi_points = [(rx1, ry1), (rx2, ry1), (rx2, ry2), (rx1, ry2)]
    else:
        roi_points = list(roi)
    if len(roi_points) < 3:
        return 0.0

    roi_image = Image.new("1", (width, height), 0)
    ImageDraw.Draw(roi_image).polygon([tuple(map(int, point)) for point in roi_points], fill=1)
    roi_mask = np.asarray(roi_image, dtype=np.uint8)
    detection_image = Image.new("1", (width, height), 0)
    detection_draw = ImageDraw.Draw(detection_image)
    if detection.polygon is not None:
        polygon = np.asarray(detection.polygon, dtype=np.int32).reshape((-1, 2))
        if len(polygon) < 3:
            return 0.0
        detection_draw.polygon([tuple(map(int, point)) for point in polygon], fill=1)
    else:
        x1, y1, x2, y2 = [int(round(v)) for v in detection.box]
        x1, x2 = sorted((max(0, min(width - 1, x1)), max(0, min(width - 1, x2))))
        y1, y2 = sorted((max(0, min(height - 1, y1)), max(0, min(height - 1, y2))))
        if x2 <= x1 or y2 <= y1:
            return 0.0
        detection_draw.rectangle((x1, y1, x2, y2), fill=1)
    detection_mask = np.asarray(detection_image, dtype=np.uint8)
    total = int(detection_mask.sum())
    inside = int(np.logical_and(detection_mask, roi_mask).sum())
    return float(inside) / total if total else 0.0


def filter_detections_by_roi(station: dict[str, Any], frame, detections: list[Detection]) -> list[Detection]:
    threshold = float(station.get("roi_min_overlap", 0.70))
    roi = roi_polygon_pixels(station, frame)
    return [d for d in detections if detection_roi_overlap(d, roi, frame.shape) >= threshold]


def draw_inspection_roi(frame, station: dict[str, Any]):
    import cv2
    import numpy as np
    output = frame.copy()
    points = roi_polygon_pixels(station, frame)
    polygon = np.asarray(points, dtype=np.int32).reshape((-1, 1, 2))
    cv2.polylines(output, [polygon], True, (255, 255, 0), 2, cv2.LINE_AA)
    x, y = points[0]
    cv2.putText(output, "INSPECTION ROI", (x + 6, max(22, y + 22)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2, cv2.LINE_AA)
    return output


V3_OBJECT_ROI_STYLE = {
    "seat1": (0, 255, 255),
    "seat2": (0, 165, 255),
    "led": (255, 0, 255),
}
V3_OBJECT_ROI_LABEL = {"seat1": "SEAT 1", "seat2": "SEAT 2", "led": "LED"}


def object_roi_polygon_pixels(station: dict[str, Any], key: str, frame):
    raw = (station.get("object_rois") or {}).get(key)
    if isinstance(raw, dict):
        raw = raw.get("polygon")
    if not isinstance(raw, list) or len(raw) < 3:
        return None
    height, width = frame.shape[:2]
    try:
        points = [(int(round(float(x) * (width - 1))), int(round(float(y) * (height - 1)))) for x, y in raw]
    except (TypeError, ValueError):
        return None
    return points if len(points) >= 3 else None


def _polygon_color_pixels(frame, points):
    """다각형 ROI 안에서 극단적인 암부·반사광을 제외한 BGR 픽셀을 반환합니다."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    if frame is None or not points:
        return None
    height,width=frame.shape[:2]
    mask_image=Image.new("1",(width,height),0)
    ImageDraw.Draw(mask_image).polygon(points,fill=1)
    mask=np.asarray(mask_image,dtype=bool)
    pixels=frame[mask]
    if len(pixels)<30:return None
    hsv=cv2.cvtColor(pixels.reshape(-1,1,3),cv2.COLOR_BGR2HSV).reshape(-1,3)
    valid=(hsv[:,2]>=10)&(hsv[:,2]<=248)
    pixels=pixels[valid]
    return pixels if len(pixels)>=30 else None


def measure_v3_roi_reference(frame, points, kind):
    """코코아·다크브라운·회색 기준색을 LAB 중앙값으로 측정합니다."""
    import cv2
    import numpy as np

    pixels=_polygon_color_pixels(frame,points)
    if pixels is None:return None
    hsv=cv2.cvtColor(pixels.reshape(-1,1,3),cv2.COLOR_BGR2HSV).reshape(-1,3)
    saturation=hsv[:,1]
    # 갈색 저장은 채도가 높은 절반, 빈 지그 저장은 채도가 낮은 70%를 사용해
    # ROI 가장자리와 조명 반사의 영향을 줄입니다.
    boundary=np.percentile(saturation,50 if kind in ("cocoa","dark") else 70)
    selected=pixels[saturation>=boundary] if kind in ("cocoa","dark") else pixels[saturation<=boundary]
    if len(selected)<20:selected=pixels
    lab=cv2.cvtColor(selected.reshape(-1,1,3),cv2.COLOR_BGR2LAB).reshape(-1,3).astype(float)
    return np.median(lab,axis=0)


def measure_v3_color_presence(station, frame, key):
    """ROI의 시트색 픽셀 비율과 유무를 반환합니다."""
    import cv2
    import numpy as np

    points=object_roi_polygon_pixels(station,key,frame)
    if not points:return None,None,"ROI 미설정"
    settings=station.get("v3_color_presence") or {}
    references=(settings.get("references") or {}).get(key) or {}
    cocoa=references.get("cocoa_lab");dark=references.get("dark_lab");gray=references.get("gray_lab")
    if not cocoa or not dark or not gray:return None,None,"기준색 미설정"
    pixels=_polygon_color_pixels(frame,points)
    if pixels is None:return None,None,"유효 픽셀 부족"
    lab=cv2.cvtColor(pixels.reshape(-1,1,3),cv2.COLOR_BGR2LAB).reshape(-1,3).astype(float)
    weight=float(settings.get("lightness_weight",0.35))
    weighted=lab.copy();weighted[:,0]*=weight
    def distance(reference):
        target=np.asarray(reference,dtype=float).copy();target[0]*=weight
        return np.linalg.norm(weighted-target,axis=1)
    seat_distance=np.minimum(distance(cocoa),distance(dark))
    gray_distance=distance(gray)
    tolerance=float(settings.get("color_tolerance",35.0))
    gray_margin=float(settings.get("gray_margin",4.0))
    seat_pixels=(seat_distance<=tolerance)&((seat_distance+gray_margin)<gray_distance)
    ratio=float(seat_pixels.mean()) if len(seat_pixels) else 0.0
    minimum=float(settings.get(f"{key}_min_ratio",0.20))
    return ratio,ratio>=minimum,None


def v3_color_presence_detections(station,frame):
    """YOLO 없이 시트1·시트2 ROI의 색상 점유율로 제품 유무를 판정합니다."""
    import numpy as np

    output=[]
    for key in ("seat1","seat2"):
        points=object_roi_polygon_pixels(station,key,frame)
        ratio,present,error=measure_v3_color_presence(station,frame,key)
        if error:
            output.append(Detection(f"{key} {error}","seat_color_unconfigured",1.0,[0,0,0,0],None))
            continue
        xs=[p[0] for p in points];ys=[p[1] for p in points]
        value="seat_unknown" if present else "seat_absent"
        output.append(Detection(f"색상비율 {ratio*100:.1f}% [{key}]",value,1.0,
                                [min(xs),min(ys),max(xs),max(ys)],np.asarray(points)))
    return output


def assign_v3_object_rois(station, frame, detections):
    """시트 검출 하나를 겹침률이 가장 큰 시트 ROI 하나에만 배정합니다."""
    seat_values = {"cocoa", "dark", "seat_unknown"}
    seats = [d for d in detections if d.value in seat_values]
    rois = {key: object_roi_polygon_pixels(station, key, frame) for key in ("seat1", "seat2")}
    if not any(rois.values()):
        return detections
    minimum = float(station.get("object_roi_min_overlap", 0.25))
    candidates = []
    for detection in seats:
        for key, polygon in rois.items():
            if polygon:
                overlap = detection_roi_overlap(detection, polygon, frame.shape)
                if overlap >= minimum:
                    candidates.append((overlap, key, detection))
    assigned_keys, assigned_ids, assigned = set(), set(), []
    for _, key, detection in sorted(candidates, key=lambda item: item[0], reverse=True):
        if key not in assigned_keys and id(detection) not in assigned_ids:
            assigned_keys.add(key); assigned_ids.add(id(detection))
            assigned.append(replace(detection, name=f"{detection.name} [{key}]"))
    return [d for d in detections if d.value not in seat_values] + assigned


def draw_v3_object_rois(frame, station):
    import cv2
    import numpy as np
    output = frame.copy()
    for key, color in V3_OBJECT_ROI_STYLE.items():
        points = object_roi_polygon_pixels(station, key, frame)
        if not points:
            continue
        polygon = np.asarray(points, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(output, [polygon], True, color, 2, cv2.LINE_AA)
        x, y = points[0]
        cv2.putText(output, V3_OBJECT_ROI_LABEL[key], (x + 5, max(20, y + 20)),
                    cv2.FONT_HERSHEY_SIMPLEX, .55, color, 2, cv2.LINE_AA)
    return output


def _center_roi(frame, box, margin=0.20):
    """바운딩박스 가장자리의 배경을 제외하고 중앙 영역만 반환합니다."""
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    dx, dy = int((x2 - x1) * margin), int((y2 - y1) * margin)
    x1, x2, y1, y2 = x1 + dx, x2 - dx, y1 + dy, y2 - dy
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2]


def _seat_lab_pixels(frame, box, station=None):
    """시트 좌/우 표면에서 금속판과 강한 반사광을 제외한 LAB 픽셀을 수집합니다."""
    import cv2
    import numpy as np

    if frame is None:
        return None
    station = station or {}
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    box_width, box_height = x2 - x1, y2 - y1
    if box_width < 4 or box_height < 4:
        return None

    # 중앙 은색 베이스를 피하고 실제 좌/우 시트 표면만 사용합니다.
    regions = station.get("seat_color_sample_regions") or [
        [0.03, 0.12, 0.43, 0.94],
        [0.57, 0.12, 0.97, 0.94],
    ]
    samples = []
    for region in regions:
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            continue
        rx1, ry1, rx2, ry2 = [float(v) for v in region]
        sx1, sx2 = x1 + int(box_width * rx1), x1 + int(box_width * rx2)
        sy1, sy2 = y1 + int(box_height * ry1), y1 + int(box_height * ry2)
        patch = frame[max(y1, sy1):min(y2, sy2), max(x1, sx1):min(x2, sx2)]
        if patch.size:
            samples.append(patch.reshape(-1, 3))
    if not samples:
        return None

    bgr = np.concatenate(samples, axis=0).reshape(-1, 1, 3).astype(np.uint8)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    min_saturation = int(station.get("seat_color_min_saturation", 18))
    min_value = int(station.get("seat_color_min_value", 18))
    max_value = int(station.get("seat_color_max_value", 235))
    mask = ((hsv[:, 1] >= min_saturation) &
            (hsv[:, 2] >= min_value) & (hsv[:, 2] <= max_value))
    # 조명 조건 때문에 채도가 낮더라도 좌/우 영역 자체는 유지하되 극단 밝기는 제외합니다.
    if int(mask.sum()) < 80:
        mask = (hsv[:, 2] >= min_value) & (hsv[:, 2] <= max_value)
    if int(mask.sum()) < 20:
        return None
    filtered = bgr.reshape(-1, 3)[mask].reshape(-1, 1, 3)
    return cv2.cvtColor(filtered, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(float)


def measure_seat_lab(frame, box, station=None):
    import numpy as np

    pixels = _seat_lab_pixels(frame, box, station)
    if pixels is None or len(pixels) == 0:
        return None
    return np.median(pixels, axis=0).astype(float)


def _robust_reference_distance(pixels, reference, fraction=0.60):
    """그림자/반사 이상치를 버리고 기준색과 가까운 다수 픽셀의 중앙 거리만 사용합니다."""
    import numpy as np

    distances = np.linalg.norm(pixels - np.asarray(reference, dtype=float), axis=1)
    keep = max(20, int(len(distances) * min(1.0, max(0.20, float(fraction)))))
    keep = min(keep, len(distances))
    closest = np.partition(distances, keep - 1)[:keep]
    return float(np.median(closest))


def classify_seat_color(station, frame, box):
    """유효 시트 영역의 대표 LAB를 저장된 두 기준색 중 가까운 쪽으로 판정합니다."""
    import numpy as np

    calibration = station.get("seat_color_calibration", {})
    cocoa = calibration.get("cocoa_lab")
    dark = calibration.get("dark_lab")
    pixels = _seat_lab_pixels(frame, box, station)
    measured = None if pixels is None else np.median(pixels, axis=0).astype(float)
    # V13.15 이전의 박스 중앙 기준값과 새 좌/우 표면 기준값을 섞지 않습니다.
    if pixels is None or calibration.get("sample_method") != 2 or not cocoa or not dark:
        return "seat_unknown", 0.0, measured
    # 픽셀별 거리를 사용하면 시트 무늬/그림자 때문에 정상 색도 max_distance를
    # 초과할 수 있습니다. 금속·반사광이 제거된 전체 표본의 중앙 LAB를 비교합니다.
    cocoa_distance = float(np.linalg.norm(measured - np.asarray(cocoa, dtype=float)))
    dark_distance = float(np.linalg.norm(measured - np.asarray(dark, dtype=float)))
    near, far = sorted((cocoa_distance, dark_distance))
    separation = (far - near) / max(far + near, 1e-6)
    force_nearest = bool(station.get("seat_color_force_nearest", True))
    if not force_nearest and (separation < float(calibration.get("min_separation", 0.10))
                              or near > float(calibration.get("max_distance", 55.0))):
        return "seat_unknown", separation, measured
    return ("cocoa" if cocoa_distance < dark_distance else "dark"), separation, measured


def enrich_seat_colors(station, frame, detections):
    output = []
    for detection in detections:
        if detection.value != "seat_unknown":
            output.append(detection)
            continue
        value, _, _ = classify_seat_color(station, frame, detection.box)
        output.append(replace(detection, value=value))
    return output


def enrich_lamp_on(station, baseline_frame, current_frame, detections):
    """현재 프레임의 LED ROI 밝기만으로 ON/OFF를 구분합니다.

    baseline_frame 인자는 이전 버전과의 호출 호환성을 위해 남겨 두지만 사용하지 않습니다.
    LED ROI는 실제 발광부에 최대한 밀착해서 설정해야 흰색 지그/반사광 영향을 줄일 수 있습니다.
    """
    import numpy as np
    from PIL import Image, ImageDraw
    brightness_required = float(station.get("led_on_brightness_threshold", 210.0))
    percentile = float(station.get("led_brightness_percentile", 90.0))

    fixed_roi = object_roi_polygon_pixels(station, "led", current_frame)
    if fixed_roi:
        height, width = current_frame.shape[:2]
        mask_image = Image.new("1", (width, height), 0)
        ImageDraw.Draw(mask_image).polygon(fixed_roi, fill=1)
        mask = np.asarray(mask_image, dtype=bool)
        # HSV의 V 값은 B/G/R 채널 최댓값과 같으므로 OpenCV 변환 없이 계산합니다.
        current_v = np.max(current_frame, axis=2)[mask]
        brightness = float(np.percentile(current_v, percentile)) if current_v.size else -1.0
        xs=[point[0] for point in fixed_roi];ys=[point[1] for point in fixed_roi]
        x,y,w,h=min(xs),min(ys),max(xs)-min(xs)+1,max(ys)-min(ys)+1
        value = "lamp_on" if brightness >= brightness_required else "lamp_off"
        kept = [d for d in detections if d.value not in ("lamp_candidate", "lamp_on", "lamp_off")]
        kept.append(Detection(f"LED ROI 밝기 {brightness:.1f}", value, 1.0, [x, y, x + w, y + h], np.asarray(fixed_roi)))
        return kept

    # AI 램프 형상으로 대신 판단하지 않습니다. ROI가 없으면 설비 설정 오류로 처리합니다.
    kept = [d for d in detections if d.value not in ("lamp_candidate", "lamp_on", "lamp_off")]
    kept.append(Detection("LED ROI 미설정", "led_unconfigured", 1.0, [0, 0, 0, 0], None))
    return kept


@dataclass
class RuntimeState:
    station_id: int
    status: str = "준비 중"
    product: int = 0
    outcome: str = "대기"
    expected: str = "-"
    actual: str = "-"
    result_code: int = 0
    detail: str = ""
    updated_at: str = "-"
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, **values: Any) -> None:
        with self.lock:
            for key, value in values.items():
                setattr(self, key, value)
            self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "station_id": self.station_id,
                "status": self.status,
                "product": self.product,
                "outcome": self.outcome,
                "expected": self.expected,
                "actual": self.actual,
                "result_code": self.result_code,
                "detail": self.detail,
                "updated_at": self.updated_at,
            }


class SegmentCamera:
    """카메라 한 대를 소유하고 끊김 시 저장 경로를 자동 재연결합니다."""

    def __init__(self, station: dict[str, Any], common: dict[str, Any], stop: threading.Event):
        self.station = station
        self.common = common
        self.stop = stop
        self.lock = threading.Lock()
        self.packet: tuple[int, float, Any, list[Detection]] | None = None
        self.error: str | None = None
        self.model_names: dict[int, str] = {}
        self.status = "준비 중"
        self.current_index: int | None = None
        self.current_name = ""
        self.retry_count = 0
        self.recover_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name=f"camera-V{station['id']}")

    def get(self):
        with self.lock:
            return self.packet, self.error

    def snapshot(self):
        with self.lock:
            return {"status": self.status, "error": self.error, "index": self.current_index,
                    "name": self.current_name, "retry_count": self.retry_count,
                    "camera_path": self.station.get("camera_path", "")}

    def settings_snapshot(self):
        with self.lock:
            return {key:self.station.get(key) for key in (
                "focus","exposure","brightness","gain","white_balance","fps",
                "autofocus","auto_exposure","auto_white_balance"
            )}

    def request_recovery(self):
        self.recover_event.set()

    def update_settings(self, values):
        with self.lock:
            for key in ("focus","exposure","brightness","gain","white_balance","fps",
                        "autofocus","auto_exposure","auto_white_balance"):
                if key in values:self.station[key] = values[key]
        self.recover_event.set()

    def _open_camera(self):
        import cv2

        camera_index, backend, identity = resolve_camera(self.station)
        with self.lock:
            self.current_index=camera_index;self.current_name=getattr(identity,"name","");self.status="연결 중"
        cap = cv2.VideoCapture(camera_index, backend)
        if not cap.isOpened():
            raise RuntimeError(
                f"카메라 열기 실패: 비전{self.station['id']} / 현재 번호 {camera_index} / "
                f"{getattr(identity, 'name', '')}. 다른 프로그램 점유와 USB 상태를 확인하세요."
            )

        resolution = str(self.station.get("resolution", "640x480")).lower().split("x")
        width, height = (int(resolution[0]), int(resolution[1])) if len(resolution) == 2 else (640, 480)
        fps = int(self.station.get("fps", 15))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        if str(self.station.get("format", "MJPG")).upper() == "MJPG":
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        autofocus=bool(self.station.get("autofocus",False))
        cap.set(cv2.CAP_PROP_AUTOFOCUS,1 if autofocus else 0)
        if not autofocus:cap.set(cv2.CAP_PROP_FOCUS,float(self.station.get("focus",100)))

        auto_exposure=bool(self.station.get("auto_exposure",False))
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE,0.75 if auto_exposure else 0.25)
        if not auto_exposure:cap.set(cv2.CAP_PROP_EXPOSURE,float(self.station.get("exposure",-5)))

        if self.station.get("brightness") is not None:
            cap.set(cv2.CAP_PROP_BRIGHTNESS,float(self.station["brightness"]))
        if self.station.get("gain") is not None:
            cap.set(cv2.CAP_PROP_GAIN,float(self.station["gain"]))
        auto_wb=bool(self.station.get("auto_white_balance",True))
        if hasattr(cv2,"CAP_PROP_AUTO_WB"):cap.set(cv2.CAP_PROP_AUTO_WB,1 if auto_wb else 0)
        if not auto_wb and self.station.get("white_balance") is not None:
            cap.set(cv2.CAP_PROP_WB_TEMPERATURE,float(self.station["white_balance"]))
        return cap, camera_index, identity

    def run(self) -> None:
        import cv2
        from ultralytics import YOLO

        role=inspection_role(self.station)
        model=None
        if role=="assembly":
            self.model_names={}
            print(f"V{self.station['id']} 검사 형식: OpenCV ROI 색상 점유율 + LED 밝기 (AI 미사용)")
        else:
            model_path=ROOT/self.station["model"]
            if not model_path.is_file():
                with self.lock:self.error=f"FileNotFoundError: 모델 파일 없음: {model_path}";self.status="모델 오류"
                return
            try:
                model=YOLO(str(model_path));validate_vision_model(self.station,model);self.model_names=dict(model.names)
                print(f"V{self.station['id']} 모델 형식: {model.task} / 표시: "
                      f"{'폴리곤 외곽선' if model.task == 'segment' else '바운딩박스'}")
            except Exception as exc:
                with self.lock:self.error=f"{type(exc).__name__}: {exc}";self.status="모델 오류"
                return
        seq=0
        while not self.stop.is_set():
            cap=None
            try:
                self.recover_event.clear()
                with CAMERA_OPEN_LOCK:
                    cap,camera_index,identity=self._open_camera()
                    ok,first_frame=cap.read()
                    if not ok or first_frame is None:raise RuntimeError("카메라 초기 프레임 읽기 실패")
                with self.lock:self.status="연결됨";self.error=None;self.retry_count=0
                print(f"V{self.station['id']} 연결: 현재 번호 {camera_index} / {getattr(identity,'name','')}")
                frame=first_frame
                while not self.stop.is_set() and not self.recover_event.is_set():
                    if seq:
                        ok,frame=cap.read()
                        if not ok or frame is None:raise RuntimeError("카메라 프레임 읽기 실패")

                    captured_at=time.monotonic()
                    if role=="assembly":
                        # 비전3 시트는 객체 검출 없이 ROI별 갈색 픽셀 비율로 유무만 판정합니다.
                        # 기존 LED ROI 밝기 판정은 검사 사이클에서 그대로 수행합니다.
                        detections=v3_color_presence_detections(self.station,frame)
                    else:
                        detections=predict_result_detections(model,self.station,frame)

                    seq+=1
                    with self.lock:self.packet=(seq,captured_at,frame.copy(),detections);self.error=None
            except Exception as exc:
                with self.lock:
                    self.retry_count+=1;self.status="복구 대기";self.error=f"{type(exc).__name__}: {exc}"
                print(f"V{self.station['id']} 복구 시도 {self.retry_count}: {self.error}")
            finally:
                if cap is not None:cap.release()
            if not self.stop.is_set():self.recover_event.wait(min(2+self.retry_count*2,15))


def expected_text(station: dict[str, Any], product: int) -> str:
    if product not in ORDER_MAP:
        return "-"
    role = inspection_role(station)
    order = ORDER_MAP[product]
    if role == "seat":
        return order["seat"]
    if role == "lamp":
        return order["lamp"]
    if role == "assembly":
        return f"시트 x{order['seat_count']} + LED ON"
    return "단품 분류"


def actual_text(detections: list[Detection], threshold: float) -> str:
    values = [d.value for d in detections if d.score >= threshold and d.value != "unknown"]
    if not values:
        return "미검출"
    counts = Counter(values)
    return ", ".join(f"{KOREAN_VALUE.get(value, value)} x{count}" for value, count in sorted(counts.items()))


def vision3_detection_text(detections: list[Detection], threshold: float) -> str:
    confident=[d for d in detections if d.score>=threshold]
    seat1=any("[seat1]" in d.name and d.value in {"cocoa","dark","seat_unknown"} for d in confident)
    seat2=any("[seat2]" in d.name and d.value in {"cocoa","dark","seat_unknown"} for d in confident)
    led_on=any(d.value=="lamp_on" for d in confident)
    led_off=any(d.value=="lamp_off" for d in confident)
    led_unconfigured=any(d.value=="led_unconfigured" for d in confident)
    led="ROI 미설정" if led_unconfigured else ("ON" if led_on else ("OFF" if led_off else "판정 대기"))
    roi_error=any(d.value in {"seat_roi_unconfigured","seat_color_unconfigured"} for d in confident)
    if roi_error:return f"시트 ROI·기준색 설정 필요 / LED {led}"
    def seat_text(key,present):
        item=next((d for d in confident if f"[{key}]" in d.name and d.value in {"seat_unknown","seat_absent"}),None)
        ratio=(item.name.split("색상비율 ",1)[1].split("%",1)[0]+"%") if item and "색상비율 " in item.name else None
        return f"{'검출' if present else '미검출'}"+(f"({ratio})" if ratio else "")
    return f"시트1 {seat_text('seat1',seat1)} / 시트2 {seat_text('seat2',seat2)} / LED {led}"


def vision3_seat_counts(raw_detections: list[Detection], used_detections: list[Detection], threshold: float) -> tuple[int, int]:
    """비전3의 두 색상 ROI 중 시트가 있다고 판정된 개수입니다."""
    seat_values = {"cocoa", "dark", "seat_unknown"}
    raw_count = sum(d.value in seat_values for d in raw_detections)
    used_count = sum(d.value in seat_values and d.score >= threshold for d in used_detections)
    return raw_count, used_count


def decide(
    station: dict[str, Any],
    detections: list[Detection],
    product: int,
    threshold: float,
) -> tuple[str, int] | None:
    role = inspection_role(station)
    confident = [d for d in detections if d.score >= threshold]

    if role == "assembly" and any(d.value in {"led_unconfigured","seat_roi_unconfigured","seat_color_unconfigured"} for d in confident):
        return "ERROR", 0

    if role == "sort":
        if not detections:
            # 빈 지그/미검출은 분류값으로 내보내지 않고 시간초과 후 ERROR 처리합니다.
            return None
        if not confident:
            return None  # 낮은 신뢰도 물체가 있으면 빈 지그로 오판하지 않습니다.
        values = [d.value for d in confident if d.value in V4_CODE]
        if len(values) != 1:
            return None
        return "OK", V4_CODE[values[0]]

    if product not in ORDER_MAP:
        return "ERROR", 0

    relevant = relevant_values(role)
    values = [d.value for d in confident if d.value in relevant]
    if not values or (role == "seat" and "seat_unknown" in values):
        return None

    seat, lamp, seat_count = order_parts(product)
    if role == "seat":
        if len(values) != 1 or values[0] not in ("cocoa", "dark"):
            return None
        return ("OK" if values[0] == seat else "NG"), 0

    if role == "lamp":
        if len(values) != 1 or values[0] not in ("round", "edge"):
            return None
        return ("OK" if values[0] == lamp else "NG"), 0

    counts = Counter(values)
    # 비전3은 주문별 시트 수량과 LED ON 두 가지만 검사합니다.
    # 차체 유무, 시트 색상, 램프 ROUND/EDGE 형상은 최종 판정에서 제외합니다.
    detected_seats = counts["cocoa"] + counts["dark"] + counts["seat_unknown"]
    ok = detected_seats == seat_count and counts["lamp_on"] == 1
    if station.get("object_rois"):
        occupied={key for d in confident if d.value in {"cocoa","dark","seat_unknown"}
                  for key in ("seat1","seat2") if f"[{key}]" in d.name}
        required={"seat1","seat2"} if seat_count==2 else {"seat1"}
        ok = ok and occupied == required
    return ("OK" if ok else "NG"), 0


@lru_cache(maxsize=16)
def find_korean_font(size: int):
    from PIL import ImageFont

    candidates = [
        Path(r"C:\Windows\Fonts\malgun.ttf"),
        Path(r"C:\Windows\Fonts\malgunsl.ttf"),
        Path(r"C:\Windows\Fonts\gulim.ttc"),
        Path(r"C:\Windows\Fonts\batang.ttc"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def draw_korean(frame, text: str, position: tuple[int, int], size: int, color: tuple[int, int, int]):
    """OpenCV 영상에 한글을 표시합니다. color는 BGR입니다."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    drawer = ImageDraw.Draw(image)
    drawer.text(position, text, font=find_korean_font(size), fill=(color[2], color[1], color[0]))
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def draw_segment_overlay(frame, detections: list[Detection], threshold: float):
    import cv2
    import numpy as np

    output = frame.copy()
    fill_layer = frame.copy()

    # 모든 마스크를 먼저 반투명으로 채웁니다.
    valid_polygons: list[tuple[Any, Detection, tuple[int, int, int]]] = []
    for detection in detections:
        if detection.polygon is None:
            continue
        polygon = np.asarray(detection.polygon, dtype=np.int32)
        if len(polygon) < 3:
            continue
        polygon = polygon.reshape((-1, 1, 2))
        color = COLORS.get(detection.value, COLORS["unknown"])
        cv2.fillPoly(fill_layer, [polygon], color)
        valid_polygons.append((polygon, detection, color))

    output = cv2.addWeighted(fill_layer, 0.25, output, 0.75, 0)

    # Segment 결과는 외곽선과 클래스명을 표시합니다.
    for polygon, detection, color in valid_polygons:
        line_color = color if detection.score >= threshold else (0, 165, 255)
        cv2.polylines(output, [polygon], True, line_color, 3, cv2.LINE_AA)
        x, y, _, _ = cv2.boundingRect(polygon)
        label = f"{detection.name} {detection.score * 100:.1f}%"
        cv2.putText(
            output,
            label,
            (x, max(22, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            line_color,
            2,
            cv2.LINE_AA,
        )

    # Detection 모델은 마스크가 없으므로 안전한 대체 표시로 바운딩박스를 사용합니다.
    # Segment 검출에는 polygon이 있으므로 중복 사각형을 그리지 않습니다.
    for detection in detections:
        if detection.polygon is not None:
            continue
        x1, y1, x2, y2 = (int(value) for value in detection.box)
        color = COLORS.get(detection.value, COLORS["unknown"])
        line_color = color if detection.score >= threshold else (0, 165, 255)
        cv2.rectangle(output, (x1, y1), (x2, y2), line_color, 3, cv2.LINE_AA)
        label = f"{detection.name} {detection.score * 100:.1f}%"
        cv2.putText(output, label, (x1, max(22, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, line_color, 2, cv2.LINE_AA)
    return output


def draw_information_panel(
    frame,
    station: dict[str, Any],
    state: RuntimeState,
    detections: list[Detection],
    threshold: float,
    write_enabled: bool,
    raw_detections: list[Detection] | None = None,
):
    import cv2

    snap = state.snapshot()
    product = int(snap["product"] or 0)
    order = ORDER_MAP.get(product, {"car": "-", "seat": "-", "lamp": "-", "seat_count": 0})
    detected = (vision3_detection_text(detections,threshold)
                if inspection_role(station)=="assembly" else actual_text(detections, threshold))
    expected = expected_text(station, product)
    best_score = max((d.score for d in detections), default=0.0)

    output = frame.copy()
    overlay = output.copy()
    panel_width = min(430, max(360, output.shape[1] - 20))
    cv2.rectangle(overlay, (10, 10), (panel_width, 280), (15, 20, 25), -1)
    output = cv2.addWeighted(overlay, 0.72, output, 0.28, 0)

    role_ko = {"seat": "시트 색상 검사", "lamp": "램프 형상 검사", "assembly": "최종 조립 검사", "sort": "단품 분류"}[inspection_role(station)]
    result_color = (0, 255, 0) if snap["outcome"] == "OK" else ((0, 0, 255) if snap["outcome"] in ("NG", "ERROR") else (255, 255, 255))
    lines = [
        (f"비전{station['id']}  {role_ko}", (255, 255, 255), 23),
        (f"상태: {snap['status']}", (255, 255, 255), 18),
        (f"주문번호: {product if product else '-'}   차종: {order['car']}", (255, 255, 255), 18),
        (f"주문 시트: {order['seat']} x{order['seat_count']}", (255, 255, 255), 18),
        (f"주문 램프: {order['lamp']}", (255, 255, 255), 18),
        (f"검사 예상값: {expected}", (255, 255, 255), 18),
        (f"실제 검출값: {detected}", (255, 255, 255), 18),
        (f"최고 신뢰도: {best_score * 100:.1f}%", (0, 220, 255), 18),
        (f"판정: {snap['outcome']}   분류값: {snap['result_code']}", result_color, 20),
        (f"PLC 쓰기: {'ON' if write_enabled else 'OFF'}", (255, 255, 255), 17),
    ]
    if inspection_role(station) == "assembly":
        raw_count, used_count = vision3_seat_counts(raw_detections or detections, detections, threshold)
        lines.insert(7, (f"시트 색상 유무: {used_count}개 / 2개 ROI", (0, 255, 255), 18))

    y = 18
    for text, color, size in lines:
        output = draw_korean(output, text, (23, y), size, color)
        y += 25
    return output


def draw_compact_information(frame, station, state, detections, threshold, write_enabled, raw_detections=None):
    """Append a small information strip; never cover the camera image."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
    snap=state.snapshot()
    product=int(snap["product"] or 0)
    role={"lamp":"램프","seat":"시트","assembly":"최종 조립","sort":"단품 분류"}[inspection_role(station)]
    confidence=max((d.score for d in detections),default=0.0)*100
    lines=[
        f"V{station['id']} {role} | 주문 {product or '-'} | 판정 {snap['outcome']} | 분류 {snap['result_code']} | 쓰기 {'ON' if write_enabled else 'OFF'}",
        f"예상: {expected_text(station,product)} | 검출: " +
        (vision3_detection_text(detections,threshold) if inspection_role(station)=="assembly" else actual_text(detections,threshold)),
        f"상태: {snap['status']} | 최고 신뢰도 {confidence:.1f}%",
    ]
    if inspection_role(station)=="assembly":
        raw_count,used_count=vision3_seat_counts(raw_detections or detections,detections,threshold)
        lines[1]=f"시트 색상 유무: {used_count}개 / 2개 ROI | {vision3_detection_text(detections,threshold)}"
    h,w=frame.shape[:2]
    strip=Image.new("RGB",(w,66),(22,27,33))
    drawer=ImageDraw.Draw(strip);font=find_korean_font(16)
    for row,line in enumerate(lines):
        if drawer.textlength(line,font=font)>w-16:
            while line and drawer.textlength(line+"…",font=font)>w-16:line=line[:-1]
            line+="…"
        color=(240,243,247)
        if row==0 and snap['outcome'] in ('NG','ERROR'):color=(255,100,100)
        elif row==0 and snap['outcome']=='OK':color=(100,240,140)
        drawer.text((8,3+row*21),line,font=font,fill=color)
    return np.concatenate((frame,cv2.cvtColor(np.asarray(strip),cv2.COLOR_RGB2BGR)),axis=0)


def make_error_frame(station_id: int, message: str):
    import cv2
    import numpy as np

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (20, 20, 20)
    frame = draw_korean(frame, f"비전{station_id} 영상 오류", (30, 55), 30, (0, 0, 255))
    frame = draw_korean(frame, message[:48], (30, 115), 18, (255, 255, 255))
    cv2.putText(frame, "CHECK CAMERA / MODEL / CONSOLE", (30, 170), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
    return frame


class FrameRenderer:
    def __init__(self, cameras, states, stations, common, write_enabled):
        self.cameras = cameras
        self.states = states
        self.stations = stations
        self.default_threshold = float(common.get("decision_confidence", 0.8))
        self.max_age = float(common.get("max_frame_age", 2))
        self.write_enabled = write_enabled

    def render(self, station_id: int, info="detail"):
        camera = self.cameras[station_id]
        packet, error = camera.get()
        if error:
            return make_error_frame(station_id, error)
        if packet is None:
            return make_error_frame(station_id, "카메라와 모델을 준비하고 있습니다")
        _, captured_at, frame, detections = packet
        if time.monotonic() - captured_at > self.max_age:
            return make_error_frame(station_id, "새 프레임이 들어오지 않습니다")
        station = self.stations[station_id]
        threshold = float(station.get("decision_confidence", self.default_threshold))
        raw_detections = list(detections)
        # ROI 편집창에는 선·마스크·문자가 없는 카메라 원본을 제공합니다.
        if info == "raw":
            return frame.copy()
        if inspection_role(station) != "assembly":
            detections = filter_detections_by_roi(station, frame, detections)
            detections = enrich_seat_colors(station, frame, detections)
        if inspection_role(station) == "assembly":
            # 비전3 일반 화면은 AI 마스크/박스와 전체 ROI를 감춰 겹침을 없앱니다.
            output = draw_v3_object_rois(frame, station) if station.get("show_object_rois",False) else frame.copy()
        else:
            output = draw_segment_overlay(frame, detections, threshold)
            output = draw_inspection_roi(output, station)
        if info=="none":return output
        if info=="compact":
            return draw_compact_information(output,self.stations[station_id],self.states[station_id],
                                            detections,threshold,self.write_enabled,raw_detections)
        return draw_information_panel(
            output,
            self.stations[station_id],
            self.states[station_id],
            detections,
            threshold,
            self.write_enabled,
            raw_detections,
        )


class MjpegServer:
    def __init__(self, renderer: FrameRenderer, stop: threading.Event, host: str, port: int, fps: int,
                 config: dict, config_path: Path):
        self.renderer = renderer
        self.stop = stop
        self.host = host
        self.port = port
        self.delay = 1.0 / max(1, fps)
        self.thread: threading.Thread | None = None
        self.http_server = None
        self.config=config;self.config_path=config_path
        self.config_lock=threading.Lock()
        self.stop_requested_via_api=threading.Event()

    def save_config(self):
        """설정 파일이 반만 기록되지 않도록 임시 파일을 거쳐 원자적으로 저장합니다."""
        temporary=self.config_path.with_suffix(self.config_path.suffix+".tmp")
        temporary.write_text(json.dumps(self.config,ensure_ascii=False,indent=2),encoding="utf-8")
        temporary.replace(self.config_path)

    def start(self):
        from flask import Flask, Response, abort, jsonify, request
        from werkzeug.serving import make_server
        import cv2

        app = Flask(__name__)

        def generator(station_id: int, info="detail"):
            while not self.stop.is_set():
                frame = self.renderer.render(station_id,info)
                ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
                if ok:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + encoded.tobytes() + b"\r\n"
                time.sleep(self.delay)

        @app.route("/vision<int:station_id>")
        def vision(station_id: int):
            if station_id not in self.renderer.cameras:
                abort(404)
            info=request.args.get("info","detail")
            if info not in ("detail","compact","none","raw"):abort(400)
            return Response(generator(station_id,info), mimetype="multipart/x-mixed-replace; boundary=frame")

        @app.route("/snapshot/vision<int:station_id>.jpg")
        def snapshot(station_id: int):
            if station_id not in self.renderer.cameras:abort(404)
            info=request.args.get("info","detail")
            if info not in ("detail","compact","none","raw"):abort(400)
            ok,encoded=cv2.imencode(".jpg",self.renderer.render(station_id,info),[int(cv2.IMWRITE_JPEG_QUALITY),82])
            if not ok:abort(500)
            return Response(encoded.tobytes(),mimetype="image/jpeg",headers={"Cache-Control":"no-store"})

        @app.route("/health")
        def health():return jsonify({"ok":True,"service":"PV5 Vision Core V13.29","pid":os.getpid()})

        @app.route("/api/status")
        def status():
            return jsonify({station_id: state.snapshot() for station_id, state in self.renderer.states.items()})

        @app.route("/api/system")
        def system_status():
            return jsonify({"visions":{i:s.snapshot() for i,s in self.renderer.states.items()},
                "cameras":{i:c.snapshot() for i,c in self.renderer.cameras.items()},
                "write_enabled":self.renderer.write_enabled,
                "opc":self.opc.snapshot()})

        @app.route("/api/opc/<action>", methods=["POST"])
        def opc_command(action):
            if request.remote_addr not in ("127.0.0.1", "::1") or request.headers.get("Origin"):
                return jsonify({"error":"OPC 조작은 비전 PC 앱에서만 가능합니다"}),403
            try:
                self.opc.submit(action, request.get_json(silent=True) or {})
                return jsonify({"accepted":True}),202
            except (ValueError, TypeError) as exc:
                return jsonify({"error":str(exc)}),400

        @app.route("/api/core/stop", methods=["POST"])
        def core_stop():
            if request.remote_addr not in ("127.0.0.1", "::1") or request.headers.get("Origin"):
                return jsonify({"error":"Core 종료는 비전 PC 앱에서만 가능합니다"}),403
            self.stop_requested_via_api.set()
            threading.Timer(0.15,self.stop.set).start()
            return jsonify({"accepted":True}),202

        @app.route("/api/cameras")
        def cameras():
            try:return jsonify([{"index":int(d.index),"name":d.name,"path":str(d.path)} for d in camera_records("DSHOW")])
            except Exception as exc:return jsonify({"error":str(exc)}),500

        @app.route("/api/camera-mapping")
        def camera_mapping():
            return jsonify({str(station_id):{
                "index":int(camera.station.get("camera",-1)),
                "path":str(camera.station.get("camera_path", "")),
            } for station_id,camera in self.renderer.cameras.items()})

        @app.route("/api/vision3/color-presence", methods=["GET"])
        def v3_color_presence_status():
            """비전3 시트 색상 유무 기준과 현재 두 ROI의 픽셀 비율을 반환합니다."""
            station=self.renderer.stations.get(3)
            if station is None:abort(404)
            settings=station.get("v3_color_presence",{})
            packet,_=self.renderer.cameras[3].get()
            live={}
            if packet is not None:
                for key in ("seat1","seat2"):
                    live[key]=measure_v3_color_presence(station,packet[2],key)
            return jsonify({"settings":settings,"live":live})

        @app.route("/api/vision3/color-presence/settings", methods=["POST"])
        def v3_color_presence_settings():
            if request.remote_addr not in ("127.0.0.1", "::1") or request.headers.get("Origin"):
                return jsonify({"error":"색상 설정은 비전 PC 앱에서만 변경할 수 있습니다"}),403
            station=self.renderer.stations.get(3)
            if station is None:abort(404)
            body=request.get_json(silent=True) or {}
            current=station.setdefault("v3_color_presence",{})
            try:
                values={
                    "seat1_min_ratio":float(body.get("seat1_min_ratio",current.get("seat1_min_ratio",.20))),
                    "seat2_min_ratio":float(body.get("seat2_min_ratio",current.get("seat2_min_ratio",.20))),
                    "color_tolerance":float(body.get("color_tolerance",current.get("color_tolerance",35))),
                    "lightness_weight":float(body.get("lightness_weight",current.get("lightness_weight",.35))),
                    "gray_margin":float(body.get("gray_margin",current.get("gray_margin",4))),
                }
                if not all(.01<=values[k]<=.95 for k in ("seat1_min_ratio","seat2_min_ratio")):
                    raise ValueError("시트 픽셀 비율은 1~95% 범위여야 합니다")
                if not 5<=values["color_tolerance"]<=100:raise ValueError("색상 허용 범위는 5~100이어야 합니다")
                if not 0<=values["lightness_weight"]<=1:raise ValueError("밝기 영향도는 0~1이어야 합니다")
                if not 0<=values["gray_margin"]<=50:raise ValueError("회색 분리 여유는 0~50이어야 합니다")
            except (TypeError,ValueError) as exc:return jsonify({"error":str(exc)}),400
            with self.config_lock:
                refs=current.get("references",{})
                current.update(values);current["references"]=refs
                self.save_config();persistent_path=save_persistent_settings(self.config,ROOT)
            return jsonify({"ok":True,"settings":current,"persistent_path":str(persistent_path)})

        @app.route("/api/vision3/color-presence/calibrate/<key>/<kind>", methods=["POST"])
        def v3_color_presence_calibrate(key,kind):
            """현재 프레임의 선택 ROI를 COCOA/DARK/빈 회색 기준으로 영구 저장합니다."""
            if request.remote_addr not in ("127.0.0.1", "::1") or request.headers.get("Origin"):
                return jsonify({"error":"색상 보정은 비전 PC 앱에서만 실행할 수 있습니다"}),403
            if key not in ("seat1","seat2") or kind not in ("cocoa","dark","gray"):abort(404)
            station=self.renderer.stations.get(3)
            packet,error=self.renderer.cameras[3].get()
            if error or packet is None:return jsonify({"error":error or "카메라 프레임이 없습니다"}),503
            # config에는 ROI가 0~1 정규화 좌표로 저장됩니다. 색상 표본 함수에는 실제
            # 카메라 픽셀 좌표를 넘겨야 하며, 변환하지 않으면 좌측 상단 1x1 픽셀만 읽게 됩니다.
            roi=object_roi_polygon_pixels(station,key,packet[2])
            if not roi:return jsonify({"error":f"{key} 검사영역을 먼저 설정하세요"}),400
            try:
                lab=measure_v3_roi_reference(packet[2],roi,kind)
                if lab is None:raise ValueError("ROI 안의 유효한 색상 픽셀이 부족합니다")
            except (ValueError,TypeError) as exc:return jsonify({"error":str(exc)}),400
            with self.config_lock:
                settings=station.setdefault("v3_color_presence",{})
                refs=settings.setdefault("references",{}).setdefault(key,{})
                refs[f"{kind}_lab"]=[round(float(value),3) for value in lab]
                self.save_config();persistent_path=save_persistent_settings(self.config,ROOT)
            ready=all(refs.get(f"{name}_lab") is not None for name in ("cocoa","dark","gray"))
            return jsonify({"ok":True,"key":key,"kind":kind,"lab":refs[f"{kind}_lab"],
                            "ready":ready,"persistent_path":str(persistent_path)})

        @app.route("/api/roi/<int:station_id>", methods=["GET", "POST"])
        def inspection_roi(station_id):
            station=self.renderer.stations.get(station_id)
            if station is None:abort(404)
            if request.method=="GET":
                return jsonify({"vision":station_id,
                    "roi":station.get("inspection_roi",[0.0,0.0,1.0,1.0]),
                    "polygon":station.get("inspection_roi_polygon"),
                    "object_rois":station.get("object_rois",{}),
                    "object_min_overlap":station.get("object_roi_min_overlap",0.25),
                    "show_object_rois":station.get("show_object_rois",False),
                    "min_overlap":station.get("roi_min_overlap",0.70)})
            body=request.get_json(silent=True) or {}
            try:
                raw_polygon=body.get("polygon")
                polygon=None
                roi=None
                if raw_polygon is not None:
                    if not isinstance(raw_polygon,list) or not 3<=len(raw_polygon)<=20:
                        raise ValueError("다각형 ROI는 3~20개의 점이 필요합니다")
                    polygon=[[float(point[0]),float(point[1])] for point in raw_polygon
                             if isinstance(point,(list,tuple)) and len(point)==2]
                    if len(polygon)!=len(raw_polygon):raise ValueError("다각형 점 좌표 형식이 잘못되었습니다")
                    flat=[value for point in polygon for value in point]
                    if not all(0.0<=value<=1.0 for value in flat):raise ValueError("ROI 좌표는 0~1 범위여야 합니다")
                    area=abs(sum(polygon[i][0]*polygon[(i+1)%len(polygon)][1]
                                 - polygon[(i+1)%len(polygon)][0]*polygon[i][1]
                                 for i in range(len(polygon)))/2.0)
                    if area<0.0025:raise ValueError("다각형 검사영역이 너무 작습니다")
                else:
                    roi=[float(v) for v in body.get("roi",[])]
                    if len(roi)!=4:raise ValueError("사각형 ROI 좌표는 4개여야 합니다")
                    x1,y1,x2,y2=roi
                    if not all(0.0<=v<=1.0 for v in roi):raise ValueError("ROI 좌표는 0~1 범위여야 합니다")
                    if x2-x1<0.05 or y2-y1<0.05:raise ValueError("검사영역이 너무 작습니다")
                min_overlap=float(body.get("min_overlap",station.get("roi_min_overlap",0.70)))
                if not 0.10<=min_overlap<=1.0:raise ValueError("겹침 비율은 0.10~1.00이어야 합니다")
                object_rois = body.get("object_rois")
                show_object_rois=bool(body.get("show_object_rois",station.get("show_object_rois",False)))
                if object_rois is not None:
                    if station_id != 3 or not isinstance(object_rois,dict):
                        raise ValueError("객체별 ROI는 비전3에서만 설정할 수 있습니다")
                    cleaned_object_rois={}
                    for key in ("seat1","seat2","led"):
                        raw=(object_rois.get(key) or {}).get("polygon") if isinstance(object_rois.get(key),dict) else object_rois.get(key)
                        if raw in (None,[]):continue
                        if not isinstance(raw,list) or not 3<=len(raw)<=20:raise ValueError(f"{key} ROI는 3~20개의 점이 필요합니다")
                        points=[[float(p[0]),float(p[1])] for p in raw if isinstance(p,(list,tuple)) and len(p)==2]
                        if len(points)!=len(raw) or not all(0<=v<=1 for p in points for v in p):raise ValueError(f"{key} ROI 좌표가 잘못되었습니다")
                        area=abs(sum(points[i][0]*points[(i+1)%len(points)][1]-points[(i+1)%len(points)][0]*points[i][1] for i in range(len(points)))/2)
                        if area<0.0005:raise ValueError(f"{key} ROI가 너무 작습니다")
                        cleaned_object_rois[key]={"polygon":[[round(v,6) for v in p] for p in points]}
                    object_min_overlap=float(body.get("object_min_overlap",station.get("object_roi_min_overlap",0.25)))
                    if not 0.05<=object_min_overlap<=1.0:raise ValueError("객체 ROI 겹침 비율은 0.05~1.00이어야 합니다")
            except (TypeError,ValueError) as exc:
                return jsonify({"error":str(exc)}),400
            with self.config_lock:
                if polygon is not None:
                    station["inspection_roi_polygon"]=[[round(v,6) for v in point] for point in polygon]
                else:
                    station["inspection_roi"]=[round(v,6) for v in roi]
                    station.pop("inspection_roi_polygon",None)
                station["roi_min_overlap"]=round(min_overlap,3)
                if object_rois is not None:
                    station["object_rois"]=cleaned_object_rois
                    station["object_roi_min_overlap"]=round(object_min_overlap,3)
                station["show_object_rois"]=show_object_rois
                self.save_config()
                persistent_path=save_persistent_settings(self.config,ROOT)
            return jsonify({"ok":True,"vision":station_id,"roi":station["inspection_roi"],
                "polygon":station.get("inspection_roi_polygon"),
                "object_rois":station.get("object_rois",{}),
                "object_min_overlap":station.get("object_roi_min_overlap",0.25),
                "show_object_rois":station.get("show_object_rois",False),
                "min_overlap":station["roi_min_overlap"],"persistent_path":str(persistent_path)})

        def apply_all_camera_mapping(raw_mapping):
            station_ids=set(self.renderer.cameras)
            try:mapping={int(key):int(value) for key,value in dict(raw_mapping).items()}
            except (TypeError,ValueError):return None,("비전별 카메라 번호 형식이 올바르지 않습니다",400)
            if set(mapping)!=station_ids:
                return None,("활성 비전1~4의 번호를 모두 선택해야 합니다",400)
            if len(set(mapping.values()))!=len(mapping):
                return None,("같은 카메라 번호를 두 비전에 배정할 수 없습니다",409)

            devices={int(device.index):device for device in camera_records("DSHOW")}
            missing=sorted(set(mapping.values())-set(devices))
            if missing:return None,(f"현재 찾을 수 없는 카메라 번호: {missing}",400)

            assignments={station_id:{"index":index,"path":str(devices[index].path),
                "name":str(devices[index].name)} for station_id,index in mapping.items()}
            with self.config_lock:
                for station_id,item in assignments.items():
                    station=self.renderer.cameras[station_id].station
                    station["camera"]=item["index"]
                    station["camera_path"]=item["path"]
                self.save_config()

            # 전체 설정을 먼저 저장한 뒤 네 작업자에게 동시에 기존 연결 해제를 요청합니다.
            # 번호 맞바꾸기 중 일시적인 점유 충돌이 생겨도 자동 재시도로 정상 연결됩니다.
            for camera in self.renderer.cameras.values():camera.request_recovery()
            return assignments,None

        @app.post("/api/cameras/bind-all")
        def bind_all():
            body=request.get_json(silent=True) or {}
            assignments,error=apply_all_camera_mapping(body.get("mapping",{}))
            if error:return jsonify({"error":error[0]}),error[1]
            return jsonify({"ok":True,"assignments":assignments})

        @app.post("/api/cameras/autofill")
        def autofill():
            # 현재 config의 고유한 camera 번호에 맞춰 최신 Windows 장치 경로를 자동 저장합니다.
            mapping={station_id:int(camera.station.get("camera",-1))
                for station_id,camera in self.renderer.cameras.items()}
            assignments,error=apply_all_camera_mapping(mapping)
            if error:return jsonify({"error":error[0]}),error[1]
            return jsonify({"ok":True,"assignments":assignments})

        @app.post("/api/camera/<int:station_id>/recover")
        def recover(station_id):
            camera=self.renderer.cameras.get(station_id)
            if camera is None:abort(404)
            camera.request_recovery();return jsonify({"ok":True,"vision":station_id})

        @app.get("/api/camera/<int:station_id>/settings")
        def get_settings(station_id):
            camera=self.renderer.cameras.get(station_id)
            if camera is None:abort(404)
            return jsonify(camera.settings_snapshot())

        @app.post("/api/camera/<int:station_id>/settings")
        def settings(station_id):
            camera=self.renderer.cameras.get(station_id)
            if camera is None:abort(404)
            body=request.get_json(silent=True) or {}
            specs={
                "focus":(0,255,int),"exposure":(-13,-1,int),
                "brightness":(0,255,float),"gain":(0,255,float),
                "white_balance":(2000,7500,int),"fps":(1,30,int),
            }
            clean={}
            try:
                for key,(minimum,maximum,converter) in specs.items():
                    if key not in body or body[key] in (None,""):continue
                    value=converter(body[key])
                    if not minimum<=value<=maximum:raise ValueError(f"{key} 허용범위 {minimum}~{maximum}")
                    clean[key]=value
                for key in ("autofocus","auto_exposure","auto_white_balance"):
                    if key in body:clean[key]=bool(body[key])
            except (TypeError,ValueError) as exc:
                return jsonify({"error":str(exc)}),400
            with self.config_lock:
                camera.update_settings(clean)
                self.save_config()
            return jsonify({"ok":True,"vision":station_id,"settings":camera.settings_snapshot(),
                "message":"설정 저장 완료. 카메라 재연결 중"})

        @app.post("/api/camera/<int:station_id>/calibrate-seat/<color>")
        def calibrate_seat(station_id, color):
            if color not in ("cocoa", "dark"):
                return jsonify({"error":"색상은 cocoa 또는 dark만 가능합니다"}),400
            camera=self.renderer.cameras.get(station_id)
            station=self.renderer.stations.get(station_id)
            if camera is None or station is None:abort(404)
            packet,error=camera.get()
            if error:return jsonify({"error":error}),409
            if packet is None:return jsonify({"error":"카메라 프레임이 아직 없습니다"}),409
            _,_,frame,detections=packet
            detections=filter_detections_by_roi(station,frame,detections)
            seats=[d for d in detections if d.value=="seat_unknown"]
            if not seats:return jsonify({"error":"화면에서 car seat를 찾지 못했습니다"}),409
            seat=max(seats,key=lambda d:max(0,d.box[2]-d.box[0])*max(0,d.box[3]-d.box[1]))
            measured=measure_seat_lab(frame,seat.box,station)
            if measured is None:return jsonify({"error":"시트 색상 영역을 측정하지 못했습니다"}),409
            with self.config_lock:
                calibration=station.setdefault("seat_color_calibration",{})
                if calibration.get("sample_method") != 2:
                    calibration.clear()
                    calibration.update({"cocoa_lab":None,"dark_lab":None,
                        "min_separation":0.10,"max_distance":55.0,"sample_method":2})
                calibration[f"{color}_lab"]=[round(float(v),3) for v in measured]
                calibration.setdefault("min_separation",0.10)
                calibration.setdefault("max_distance",55.0)
                calibration["sample_method"]=2
                self.save_config()
                persistent_path=save_persistent_settings(self.config, ROOT)
            return jsonify({"ok":True,"vision":station_id,"color":color,
                "lab":calibration[f"{color}_lab"],"persistent_path":str(persistent_path),
                "ready":bool(calibration.get("cocoa_lab") and calibration.get("dark_lab")),
                "method":"좌우 시트 표면/금속판·반사광 제외",
                "message":f"{color.upper()} 기준색 영구 저장 완료"})

        @app.post("/api/camera/<int:station_id>/bind")
        def bind(station_id):
            camera=self.renderer.cameras.get(station_id)
            if camera is None:abort(404)
            index=int((request.get_json(silent=True) or {}).get("index",-1))
            used_by=[other_id for other_id,other in self.renderer.cameras.items()
                if other_id!=station_id and int(other.station.get("camera",-999))==index]
            if used_by:
                return jsonify({"error":f"현재 번호 {index}은 비전{used_by[0]}에서 사용 중입니다. 전체 배정 저장을 사용하세요."}),409
            devices=[d for d in camera_records("DSHOW") if int(d.index)==index]
            if len(devices)!=1:return jsonify({"error":"해당 현재 번호의 카메라를 찾지 못했습니다"}),400
            with self.config_lock:
                camera.station["camera"]=index;camera.station["camera_path"]=str(devices[0].path)
                self.save_config()
            camera.request_recovery()
            return jsonify({"ok":True,"vision":station_id,"index":index,"path":str(devices[0].path)})

        @app.route("/")
        def index():
            cards = "".join(
                f'<section><h2>비전{station_id}</h2><img src="/vision{station_id}"></section>'
                for station_id in sorted(self.renderer.cameras)
            )
            return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>PV5 비전 모니터</title>
<style>
body{{background:#1c2228;color:white;font-family:'Malgun Gothic',sans-serif;margin:18px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
section{{background:#0d1115;border:1px solid #536270;padding:10px;border-radius:8px}}
h1,h2{{margin:6px 0 10px}} img{{width:100%;height:auto;display:block}}
</style></head><body><h1>PV5 비전 실시간 검사</h1><div class="grid">{cards}</div></body></html>"""

        self.http_server = make_server(self.host, self.port, app, threaded=True)
        self.thread = threading.Thread(target=self.http_server.serve_forever, daemon=True, name="mjpeg-server")
        self.thread.start()

    def shutdown(self):
        if self.http_server is not None:
            self.http_server.shutdown()


class OpcIO:
    def __init__(self, client, station, state, write_enabled, logger):
        self.client = client
        self.station = station
        self.state = state
        self.write_enabled = write_enabled
        self.logger = logger
        self.nodes = {}
        self.types = {}

    async def prepare(self):
        from asyncua import ua

        tags = self.station["tags"]
        required = ["start", "running", "finish", "ng", "error"]
        required += ["result"] if self.station["id"] == 4 else ["product"]
        for key in required:
            node_id = tags.get(key)
            if not node_id:
                raise ValueError(f"V{self.station['id']} {key} NodeId가 없습니다")
            node = self.client.get_node(node_id)
            self.nodes[key] = node
            self.types[key] = await node.read_data_type_as_variant_type()
            await self.read(key)
            if key not in ("start", "product", "discharge_complete") and self.write_enabled:
                access = await node.get_user_access_level()
                if ua.AccessLevel.CurrentWrite not in access:
                    raise ValueError(f"V{self.station['id']} {key} 태그에 쓰기 권한이 없습니다")

    async def read(self, key):
        value = await self.nodes[key].read_data_value(raise_on_bad_status=False)
        if not value.StatusCode.is_good():
            raise RuntimeError(f"{key} 품질={value.StatusCode.name}")
        return value.Value.Value

    async def put(self, key, value):
        from asyncua import ua

        if self.write_enabled:
            await self.nodes[key].write_value(ua.DataValue(ua.Variant(value, self.types[key])))
        self.logger(self.station["id"], "write" if self.write_enabled else "dry_write", {key: value})

    async def reset(self):
        for key in ("finish", "running", "ng", "error"):
            await self.put(key, False)
        # 비전4 CLASS_RESULT는 PLC가 초기화합니다. Python은 JIG_Dispose를
        # 확인하지 않고 FINISH/ERROR 등의 비트 신호만 OFF합니다.
        if self.station["id"] == 4:
            self.state.update(outcome="대기", actual="-", detail="")
        else:
            self.state.update(outcome="대기", result_code=0, actual="-", detail="")

    async def finish(self, result, detail):
        outcome, code = result
        # 판정이 끝나는 순간 RUNNING을 먼저 OFF한 뒤 결과 신호를 출력합니다.
        await self.put("running", False)
        await self.put("finish", False)
        await self.put("ng", False)
        await self.put("error", False)
        if self.station["id"] == 4:
            valid_part = outcome == "OK" and code in (2, 3, 4, 5, 6)
            await self.put("result", code if valid_part else 0)
            if valid_part:
                await self.put("finish", True)
            else:
                await self.put("error", True)
        else:
            # V1~V3은 FINISH/NG/ERROR 중 판정에 해당하는 신호 하나만 ON합니다.
            if outcome == "OK":await self.put("finish", True)
            elif outcome == "NG":await self.put("ng", True)
            else:await self.put("error", True)
        self.state.update(status="검사 완료", outcome=outcome, result_code=code, detail=detail)


async def station_cycle(io: OpcIO, camera: SegmentCamera, common, stop):
    station = io.station
    station_id = station["id"]
    # 비전별 기준이 있으면 우선 사용합니다. 비전3은 수량 검출을 위해 0.60을 사용합니다.
    threshold = float(station.get("decision_confidence", common.get("decision_confidence", 0.8)))
    stable_frames = int(station.get("stable_frames", common.get("stable_frames", 5)))
    max_age = float(common.get("max_frame_age", 2))
    timeout = float(common.get("timeout_seconds", 10))
    result_pulse = max(0.1, float(common.get("result_pulse_seconds", 1.0)))
    lamp_on_delay = float(common.get("vision3_lamp_on_delay_seconds", 0.0))

    try:
        await io.prepare()
        io.state.update(status="카메라 및 모델 준비 중")
        deadline = time.monotonic() + 60
        while not stop.is_set():
            packet, error = camera.get()
            if error:
                raise RuntimeError(error)
            if packet and time.monotonic() - packet[1] <= max_age:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("카메라 또는 모델 준비 시간초과")
            await asyncio.sleep(0.1)

        # 시작할 때 START가 이미 ON이면 이전 요청으로 보고 OFF까지 대기합니다.
        while not stop.is_set() and await io.read("start"):
            io.state.update(status="START OFF 대기")
            await asyncio.sleep(0.1)

        await io.reset()

        while not stop.is_set():
            io.state.update(status="START ON 대기")
            baseline_packet, _ = camera.get()
            baseline_frame = baseline_packet[2].copy() if baseline_packet is not None else None
            while not stop.is_set() and not await io.read("start"):
                baseline_packet, _ = camera.get()
                if baseline_packet is not None:
                    baseline_frame = baseline_packet[2].copy()
                # 대기 중에도 주문번호를 화면에 표시합니다.
                if station_id != 4:
                    try:
                        product = int(await io.read("product"))
                        io.state.update(product=product, expected=expected_text(station, product))
                    except Exception:
                        pass
                await asyncio.sleep(0.1)
            if stop.is_set():
                return

            started_at = time.monotonic()
            product = 0 if station_id == 4 else int(await io.read("product"))
            order_error = None
            if station_id != 4:
                try:
                    order_parts(product)
                except (TypeError, ValueError) as exc:
                    # 주문값 0/비정상 값 때문에 작업 루프 전체가 종료되지 않도록 이번 사이클만 ERROR 처리합니다.
                    order_error = str(exc)

            await io.reset()
            await io.put("running", True)
            io.state.update(
                status="검사 중",
                product=product,
                expected=expected_text(station, product),
                outcome="판정 중",
                result_code=0,
            )

            result = ("ERROR", 0) if order_error else None
            detail = f"주문값 오류: {order_error}" if order_error else "안정된 검출 없음 또는 시간초과"
            signature = None
            streak = 0
            last_seq = -1

            while not stop.is_set() and result is None and time.monotonic() - started_at < timeout:
                if not await io.read("start"):
                    result = ("ERROR", 0)
                    detail = "검사 중 START 조기 OFF"
                    break
                if station_id != 4 and int(await io.read("product")) != product:
                    result = ("ERROR", 0)
                    detail = "검사 중 주문번호 변경"
                    break

                # 비전3: PLC START 후 램프가 켜질 시간을 확보한 뒤 새 프레임만 판정합니다.
                if inspection_role(station) == "assembly" and time.monotonic() < started_at + lamp_on_delay:
                    remaining = max(0.0, started_at + lamp_on_delay - time.monotonic())
                    io.state.update(status=f"램프 점등 대기 {remaining:.1f}초")
                    await asyncio.sleep(min(0.1, remaining or 0.1))
                    continue
                if inspection_role(station) == "assembly":
                    io.state.update(status="점등 및 부품 누락 검사 중")

                packet, error = camera.get()
                if error:
                    result = ("ERROR", 0)
                    detail = error
                    break
                if packet:
                    seq, captured_at, frame, detections = packet
                    valid_after = started_at + lamp_on_delay if inspection_role(station) == "assembly" else started_at
                    if seq != last_seq and captured_at >= valid_after and time.monotonic() - captured_at <= max_age:
                        last_seq = seq
                        if inspection_role(station) == "assembly":
                            detections = enrich_lamp_on(station, baseline_frame, frame, detections)
                        else:
                            detections = filter_detections_by_roi(station, frame, detections)
                            detections = enrich_seat_colors(station, frame, detections)
                        candidate = decide(station, detections, product, threshold)
                        current_actual = (vision3_detection_text(detections,threshold)
                                          if inspection_role(station)=="assembly" else actual_text(detections, threshold))
                        io.state.update(actual=current_actual)
                        # 신뢰도는 프레임마다 조금씩 흔들리므로 안정 판정 서명에는
                        # 클래스 구성만 사용합니다.
                        sig = (candidate, tuple(sorted(d.value for d in detections))) if candidate else None
                        streak = streak + 1 if sig and sig == signature else (1 if sig else 0)
                        signature = sig
                        if streak >= stable_frames:
                            result = candidate
                            detail = f"{stable_frames}개 연속 프레임 판정"
                            break
                await asyncio.sleep(0.04)

            if result is None:
                result = ("ERROR", 0)

            if not await io.read("start"):
                await io.reset()
                continue
            await io.finish(result, detail)

            # FINISH/NG/ERROR 비트만 정확히 설정 시간 동안 출력합니다.
            # 비전4 CLASS_RESULT는 Python이 0으로 지우지 않고 PLC가 초기화합니다.
            pulse_until = time.monotonic() + result_pulse
            while not stop.is_set() and time.monotonic() < pulse_until:
                remaining = max(0.0, pulse_until - time.monotonic())
                io.state.update(status=f"검사 결과 출력 {remaining:.1f}초")
                await asyncio.sleep(min(0.1, remaining or 0.01))
            if stop.is_set():return
            await io.reset()

            # START가 계속 ON인 동안 같은 제품을 다시 검사하지 않습니다.
            while not stop.is_set() and await io.read("start"):
                io.state.update(status="다음 검사 준비 / START OFF 대기")
                await asyncio.sleep(0.1)

    except Exception as exc:
        io.state.update(status="연동 중단", outcome="ERROR", detail=str(exc))
        io.logger(station_id, "blocked", {"error": str(exc)})
        print(f"V{station_id} 연동 중단: {exc}")


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def validate_config(config):
    stations = [s for s in config["stations"] if s.get("enabled", True)]
    ids = [int(s["id"]) for s in stations]
    cameras = [int(s["camera"]) for s in stations]
    if not stations:
        raise ValueError("활성화된 비전이 없습니다")
    if len(ids) != len(set(ids)) or any(i not in (1, 2, 3, 4) for i in ids):
        raise ValueError("비전 ID는 1~4이며 중복될 수 없습니다")
    if len(cameras) != len(set(cameras)):
        raise ValueError("활성화된 비전의 카메라 번호가 중복되어 있습니다")
    return stations


async def run_application(args):
    import cv2

    config_path = ROOT / args.config
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    persistent_path = load_persistent_settings(config, ROOT)
    print("사용자 색상 설정:", persistent_path)
    stations_list = validate_config(config)
    stations = {int(s["id"]): s for s in stations_list}
    stop = threading.Event()
    states = {station_id: RuntimeState(station_id) for station_id in stations}
    cameras={station_id:SegmentCamera(station,config,stop) for station_id,station in stations.items()}

    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_segment_mjpeg.jsonl")

    def logger(station_id, event, data):
        record = {"time": datetime.now().isoformat(), "station": station_id, "event": event, **data}
        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    for camera in cameras.values():
        camera.thread.start()

    renderer = FrameRenderer(cameras, states, stations, config, args.write)
    mjpeg=MjpegServer(renderer,stop,args.host,args.port,args.stream_fps,config,config_path)
    from opc_control import OpcController
    opc = OpcController(asyncio.get_running_loop(), config, stations, states, cameras,
                        stop, renderer, OpcIO, station_cycle, logger)
    mjpeg.opc = opc
    mjpeg.start()

    print("\nSCADA MJPEG 주소")
    print(f"전체 화면 : http://{local_ip()}:{args.port}/")
    for station_id in sorted(stations):
        print(f"비전{station_id}    : http://{local_ip()}:{args.port}/vision{station_id}")
    print(f"상태 JSON : http://{local_ip()}:{args.port}/api/status")
    print("종료: 미리보기 창에서 Q 또는 ESC, 아니면 Ctrl+C\n")

    try:
        if args.no_opc:
            for station_id, state in states.items():
                product = args.demo_product if station_id != 4 else 0
                state.update(
                    status="OPC 미연결 시험",
                    product=product,
                    expected=expected_text(stations[station_id], product),
                    outcome="화면 시험",
                )
        else:
            await opc.execute("connect", config["endpoint"], args.write)

        while not stop.is_set():
            if args.preview:
                for station_id in sorted(stations):
                    cv2.imshow(f"PV5 V{station_id} SEGMENT", renderer.render(station_id))
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    stop.set()
                    break
            await asyncio.sleep(0.03)

    finally:
        stop.set()
        try:
            await opc.shutdown()
        finally:
            mjpeg.shutdown()
            cv2.destroyAllWindows()
            for camera in cameras.values():
                camera.thread.join(timeout=1)
        print("프로그램 종료. PLC 출력 상태를 반드시 확인하세요.")
        print("로그:", log_path)
        if mjpeg.stop_requested_via_api.is_set():
            # Native camera/AI libraries may keep interpreter threads alive after
            # Python cleanup has finished. Release the owned Core process here.
            import sys
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json", help="V13 설정 파일")
    parser.add_argument("--host", default="0.0.0.0", help="MJPEG 서버 수신 주소")
    parser.add_argument("--port", type=int, default=5000, help="MJPEG 서버 포트")
    parser.add_argument("--stream-fps", type=int, default=8, help="SCADA 영상 FPS")
    parser.add_argument("--preview", action="store_true", help="비전 PC에도 창 표시")
    parser.add_argument("--write", action="store_true", help="실제 PLC 출력 태그 쓰기 활성화")
    parser.add_argument("--no-opc", action="store_true", help="OPC-UA 없이 카메라와 화면만 시험")
    parser.add_argument("--demo-product", type=int, default=1, choices=range(1, 9), help="OPC 없는 시험 주문번호")
    return parser.parse_args()


def acquire_single_instance():
    import os
    if os.name != "nt":return None
    import ctypes
    handle=ctypes.windll.kernel32.CreateMutexW(None,False,"Global\\PV5_VISION_CORE_SERVER_V13")
    if ctypes.windll.kernel32.GetLastError()==183:raise RuntimeError("Vision Core 서버가 이미 실행 중입니다")
    return handle


if __name__ == "__main__":
    _mutex=acquire_single_instance()
    arguments = parse_args()
    try:
        asyncio.run(run_application(arguments))
    except KeyboardInterrupt:
        print("사용자 종료")
    except Exception as exc:
        print(f"실행 오류: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
