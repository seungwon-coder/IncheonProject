"""Dobot 알람 번호를 화면과 로그에 쓰기 쉬운 형식으로 변환한다."""

from __future__ import annotations

from typing import Iterable


def alarm_details(codes: Iterable[int]) -> list[dict[str, object]]:
    """알람 번호에 16진수 표기를 함께 붙인다.

    제조사별 상세 설명표는 이후 추가할 수 있다. 현재는 잘못된 설명을 보여주지
    않도록 확인된 숫자와 16진수만 반환한다.
    """
    details: list[dict[str, object]] = []
    for code in codes:
        numeric_code = int(code)
        if numeric_code < 0:
            raise ValueError("알람 코드는 0 이상이어야 합니다.")
        details.append({"code": numeric_code, "hex": f"0x{numeric_code:02X}"})
    return details
