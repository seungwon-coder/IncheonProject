"""Dobot Worker가 읽은 최신 좌표를 메인 프로세스와 안전하게 공유한다.

새 COM 연결을 만들지 않고 기존 SDK 소유 프로세스가 읽은 GetPose 결과만 복사한다.
따라서 SCADA 좌표 전송 기능이 로봇 제어권을 빼앗거나 이동 명령을 만들지 않는다.
"""

from __future__ import annotations

import math
import time
from typing import Any


POSITION_AXES = ("x", "y", "z", "r", "j1", "j2", "j3", "j4")


def publish_position(cache: Any, raw_pose: Any) -> None:
    """SDK의 X/Y/Z/R/J1~J4 값을 공유 메모리에 최신값으로 저장한다."""
    if cache is None or raw_pose is None or len(raw_pose) < 8:
        return
    try:
        values = [float(value) for value in raw_pose[:8]]
        if not all(math.isfinite(value) for value in values):
            return
        lock = cache.get_lock()
        if not lock.acquire(False):
            return
        try:
            data = cache.get_obj()
            data[:8] = values
            data[8] = time.monotonic()
        finally:
            lock.release()
    except (TypeError, ValueError, OSError):
        # 모니터링 저장 실패가 실제 로봇 명령 결과를 바꾸면 안 된다.
        return


def read_position(cache: Any, max_age_seconds: float = 3.0) -> dict[str, float] | None:
    """정해진 시간보다 오래되지 않은 최신 좌표만 반환한다."""
    if cache is None or max_age_seconds <= 0:
        return None
    lock = cache.get_lock()
    if not lock.acquire(False):
        return None
    try:
        data = list(cache.get_obj())
    finally:
        lock.release()
    age = time.monotonic() - data[8]
    if data[8] <= 0 or age < 0 or age > max_age_seconds:
        return None
    return dict(zip(POSITION_AXES, data[:8]))


class PositionCaptureSdk:
    """기존 SDK 호출은 그대로 전달하고 GetPose 결과만 공유 메모리에 복사한다."""

    def __init__(self, sdk: Any, cache: Any) -> None:
        self.sdk = sdk
        self.cache = cache
        self.next_idle_read = 0.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.sdk, name)

    def GetPose(self, api: Any) -> Any:  # SDK 함수명이라 대문자 이름을 유지한다.
        raw_pose = self.sdk.GetPose(api)
        publish_position(self.cache, raw_pose)
        self.next_idle_read = time.monotonic() + 1.0
        return raw_pose

    def sample_idle(self, api: Any) -> None:
        """로봇이 대기 중이면 최대 1초에 한 번 현재 좌표를 새로 읽는다."""
        if time.monotonic() < self.next_idle_read:
            return
        try:
            self.GetPose(api)
        except Exception:
            # 마지막 정상값은 유지하고, 읽는 쪽에서 오래된 값인지 판정한다.
            self.next_idle_read = time.monotonic() + 1.0
