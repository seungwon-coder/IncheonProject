"""
PV5 V13.5 Vision Core: 비전1~4 YOLO + OPC-UA + 자동복구 + SCADA MJPEG

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
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from camera_identity import camera_records, resolve_camera


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
        "assembly": {"cocoa", "dark", "seat_unknown", "round", "edge"},
        "sort": {"empty", "cocoa", "dark", "round", "edge", "body", "seat_unknown"},
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
    if not values.intersection(relevant_values(role)):
        raise ValueError(
            f"V{station['id']} 모델 클래스가 검사 역할({role})과 연결되지 않았습니다. "
            f"모델 클래스={list(model.names.values())}"
        )


@dataclass
class Detection:
    name: str
    value: str
    score: float
    box: list[float]
    polygon: Any


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
                    result=model.predict(frame,conf=float(self.station.get("display_confidence",.25)),
                        imgsz=int(self.station.get("imgsz",640)),device=self.station.get("device","cpu"),verbose=False)[0]

                    detections: list[Detection]=[]
                    if result.boxes is not None:
                        classes=result.boxes.cls.cpu().tolist();scores=result.boxes.conf.cpu().tolist()
                        boxes=result.boxes.xyxy.cpu().tolist()
                        polygons=result.masks.xy if result.masks is not None else []
                        count=min(len(classes),len(scores),len(boxes))

                        for index in range(count):
                            class_name=model.names[int(classes[index])]
                            detections.append(Detection(name=class_name,value=mapped_value(self.station,class_name),
                                score=float(scores[index]),box=boxes[index],
                                polygon=polygons[index] if index < len(polygons) else None))

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
        return f"{order['seat']} x{order['seat_count']} + {order['lamp']} x1"
    return "단품 분류"


def actual_text(detections: list[Detection], threshold: float) -> str:
    values = [d.value for d in detections if d.score >= threshold and d.value != "unknown"]
    if not values:
        return "미검출"
    counts = Counter(values)
    return ", ".join(f"{KOREAN_VALUE.get(value, value)} x{count}" for value, count in sorted(counts.items()))


def decide(
    station: dict[str, Any],
    detections: list[Detection],
    product: int,
    threshold: float,
) -> tuple[str, int] | None:
    role = inspection_role(station)
    confident = [d for d in detections if d.score >= threshold]

    if role == "sort":
        if not confident:
            return "OK", 1  # 연속된 미검출은 빈 지그
        values = [d.value for d in confident if d.value in V4_CODE]
        if len(values) != 1:
            return None
        return "OK", V4_CODE[values[0]]

    if product not in ORDER_MAP:
        return "ERROR", 0

    relevant = relevant_values(role)
    values = [d.value for d in confident if d.value in relevant]
    if not values or "seat_unknown" in values:
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
    expected = Counter({seat: seat_count, lamp: 1})
    return ("OK" if counts == expected else "NG"), 0


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
):
    import cv2

    snap = state.snapshot()
    product = int(snap["product"] or 0)
    order = ORDER_MAP.get(product, {"car": "-", "seat": "-", "lamp": "-", "seat_count": 0})
    detected = actual_text(detections, threshold)
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

    y = 18
    for text, color, size in lines:
        output = draw_korean(output, text, (23, y), size, color)
        y += 25
    return output


def draw_compact_information(frame, station, state, detections, threshold, write_enabled):
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
        f"예상: {expected_text(station,product)} | 검출: {actual_text(detections,threshold)}",
        f"상태: {snap['status']} | 최고 신뢰도 {confidence:.1f}%",
    ]
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


def draw_small_information(frame, station, state, detections, threshold, write_enabled):
    """Show the original inspection details in a smaller box inside the image."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    snap=state.snapshot()
    product=int(snap["product"] or 0)
    order=ORDER_MAP.get(product,{"car":"-","seat":"-","lamp":"-","seat_count":0})
    role={"seat":"시트 색상 검사","lamp":"램프 형상 검사",
          "assembly":"최종 조립 검사","sort":"단품 분류"}[inspection_role(station)]
    result_color=(100,240,140) if snap["outcome"]=="OK" else (
        (255,100,100) if snap["outcome"] in ("NG","ERROR") else (240,243,247))
    lines=[
        f"비전{station['id']}  {role}",
        f"상태: {snap['status']}",
        f"주문번호: {product or '-'}  차종: {order['car']}",
        f"주문 시트: {order['seat']} x{order['seat_count']}",
        f"주문 램프: {order['lamp']}",
        f"검사 예상값: {expected_text(station,product)}",
        f"실제 검출값: {actual_text(detections,threshold)}",
        f"최고 신뢰도: {max((d.score for d in detections),default=0)*100:.1f}%",
        f"판정: {snap['outcome']}  분류값: {snap['result_code']}",
        f"PLC 쓰기: {'ON' if write_enabled else 'OFF'}",
    ]
    h,w=frame.shape[:2]
    margin=8; font=find_korean_font(14); line_height=18
    box_width=min(340,w-2*margin); box_height=min(10+len(lines)*line_height,h-2*margin)
    if box_width<30 or box_height<30:return frame
    # Convert once per frame, including a translucent background and all text.
    rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
    image=Image.fromarray(rgb)
    layer=Image.new("RGBA",image.size,(0,0,0,0))
    drawer=ImageDraw.Draw(layer)
    drawer.rectangle((margin,margin,margin+box_width,margin+box_height),fill=(15,20,25,155))
    for row,line in enumerate(lines):
        y=margin+5+row*line_height
        if y+line_height>margin+box_height:break
        if drawer.textlength(line,font=font)>box_width-12:
            while line and drawer.textlength(line+"…",font=font)>box_width-12:line=line[:-1]
            line+="…"
        color=result_color if row==8 else ((255,220,90) if row==7 else (240,243,247))
        drawer.text((margin+6,y),line,font=font,fill=(*color,255))
    return cv2.cvtColor(np.asarray(Image.alpha_composite(image.convert("RGBA"),layer).convert("RGB")),
                        cv2.COLOR_RGB2BGR)


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
        self.threshold = float(common.get("decision_confidence", 0.8))
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
        output = draw_segment_overlay(frame, detections, self.threshold)
        if info=="none":return output
        if info=="compact":
            return draw_compact_information(output,self.stations[station_id],self.states[station_id],
                                            detections,self.threshold,self.write_enabled)
        if info=="small":
            return draw_small_information(output,self.stations[station_id],self.states[station_id],
                                          detections,self.threshold,self.write_enabled)
        return draw_information_panel(
            output,
            self.stations[station_id],
            self.states[station_id],
            detections,
            self.threshold,
            self.write_enabled,
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
            info=request.args.get("info","small")
            if info not in ("detail","compact","small","none"):abort(400)
            return Response(generator(station_id,info), mimetype="multipart/x-mixed-replace; boundary=frame")

        @app.route("/snapshot/vision<int:station_id>.jpg")
        def snapshot(station_id: int):
            if station_id not in self.renderer.cameras:abort(404)
            info=request.args.get("info","detail")
            if info not in ("detail","compact","small","none"):abort(400)
            ok,encoded=cv2.imencode(".jpg",self.renderer.render(station_id,info),[int(cv2.IMWRITE_JPEG_QUALITY),82])
            if not ok:abort(500)
            return Response(encoded.tobytes(),mimetype="image/jpeg",headers={"Cache-Control":"no-store"})

        @app.route("/health")
        def health():return jsonify({"ok":True,"service":"PV5 Vision Core V13.7","pid":os.getpid()})

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
                f'<section><h2>비전{station_id}</h2><img src="/vision{station_id}?info=small"></section>'
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
            if key not in ("start", "product") and self.write_enabled:
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
        if self.station["id"] == 4:
            await self.put("result", 0)
        self.state.update(outcome="대기", result_code=0, actual="-", detail="")

    async def finish(self, result, detail):
        outcome, code = result
        if self.station["id"] == 4:
            await self.put("result", code if outcome == "OK" else 0)
        await self.put("ng", outcome == "NG")
        await self.put("error", outcome == "ERROR")
        await self.put("running", False)
        await self.put("finish", True)  # PLC가 이 신호 이후 결과를 읽습니다.
        self.state.update(status="검사 완료", outcome=outcome, result_code=code, detail=detail)


async def station_cycle(io: OpcIO, camera: SegmentCamera, common, stop):
    station = io.station
    station_id = station["id"]
    threshold = float(common.get("decision_confidence", 0.8))
    stable_frames = int(common.get("stable_frames", 5))
    max_age = float(common.get("max_frame_age", 2))
    timeout = float(common.get("timeout_seconds", 10))

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
            while not stop.is_set() and not await io.read("start"):
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
            if station_id != 4:
                order_parts(product)

            await io.reset()
            await io.put("running", True)
            io.state.update(
                status="검사 중",
                product=product,
                expected=expected_text(station, product),
                outcome="판정 중",
            )

            result = None
            detail = "안정된 검출 없음 또는 시간초과"
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

                packet, error = camera.get()
                if error:
                    result = ("ERROR", 0)
                    detail = error
                    break
                if packet:
                    seq, captured_at, _, detections = packet
                    if seq != last_seq and captured_at >= started_at and time.monotonic() - captured_at <= max_age:
                        last_seq = seq
                        candidate = decide(station, detections, product, threshold)
                        current_actual = actual_text(detections, threshold)
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

            # PLC가 START를 0으로 내려 결과 수신을 확인할 때까지 결과를 유지합니다.
            while not stop.is_set() and await io.read("start"):
                await asyncio.sleep(0.1)
            if not stop.is_set():
                await io.reset()

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
