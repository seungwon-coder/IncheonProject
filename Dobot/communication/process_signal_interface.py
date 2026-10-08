"""GUI 모의 입력과 OPC-DA 입력을 같은 공정 신호로 변환한다.

공정 상태 머신은 신호가 GUI에서 왔는지 KEPServerEX에서 왔는지 알 필요가 없다.
두 입력 방식 모두 이 파일을 거쳐 ``ProcessSignals``를 만들기 때문에, 나중에
실제 OPC-DA 연결을 추가해도 로봇의 조립·반환 공정 코드는 수정하지 않는다.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping, Protocol

from communication.opc_tag_contract import PRODUCT_TYPE_CODES, TagDirection, tags_for, validate_value
from robot_control.process_state_machine import ProcessSignals


class ProcessSignalProvider(Protocol):
    """입력 장치 종류와 관계없이 상태 머신에 신호를 제공하는 공통 규칙."""

    def read(self, robot_name: str) -> ProcessSignals:
        """선택 로봇의 현재 공정 입력을 한 묶음으로 반환한다."""


class GuiSignalProvider:
    """통합 GUI가 가진 모의 신호를 공통 형식으로 읽는다."""

    def __init__(self, source: Any) -> None:
        # MockSignalSource와 결합하되 상속하지 않아 두 모듈의 역할을 분리한다.
        self.source = source

    def read(self, robot_name: str) -> ProcessSignals:
        return self.source.snapshot(robot_name)


class OpcTagReader(Protocol):
    """향후 OPC-DA 라이브러리가 구현해야 하는 최소 읽기 기능."""

    def read_tags(self, robot_name: str) -> Mapping[str, Any]:
        """논리 태그 이름과 현재 값의 사전을 반환한다."""


class OpcSignalProvider:
    """KEPServer 논리 태그를 검사하고 공통 공정 신호로 변환한다."""

    def __init__(self, reader: OpcTagReader) -> None:
        self.reader = reader

    def read(self, robot_name: str) -> ProcessSignals:
        raw_values = dict(self.reader.read_tags(robot_name))
        definitions = {
            tag.name: tag
            for tag in tags_for(robot_name)
            if tag.direction is TagDirection.INPUT
        }

        # 누락된 OPC 태그는 계약에 정해 둔 안전한 기본값(False, 0 또는 1)을 쓴다.
        checked: dict[str, bool | int | str] = {}
        for name, definition in definitions.items():
            # Dobot_4 베이스 출고 START는 AutomaticOperation이 별도 사이클로
            # 소비하므로 일반 제품 ProcessSignals에는 넣지 않는다.
            if name == "command.base_start":
                continue
            value = raw_values.get(name, definition.default)
            field_name = "start" if name == "command.start" else name.removeprefix("input.")
            checked[field_name] = validate_value(definition, value)

        # PLC에서는 제품 종류를 정수로 보내고, 상태 머신에서는 읽기 쉬운 이름을 쓴다.
        if robot_name == "Dobot_4":
            product_code = int(checked.pop("product_type"))
            try:
                checked["product_type"] = PRODUCT_TYPE_CODES[product_code]
            except KeyError as exc:
                allowed = ", ".join(str(code) for code in PRODUCT_TYPE_CODES)
                raise ValueError(
                    f"Dobot_4 제품 코드는 {allowed} 중 하나여야 합니다."
                ) from exc

        return ProcessSignals(**checked)


def signals_as_dict(provider: ProcessSignalProvider, robot_name: str) -> dict[str, Any]:
    """GUI 표시나 진단 로그에서 사용할 수 있도록 신호를 사전으로 반환한다."""
    return asdict(provider.read(robot_name))
