"""OPC-DA 통신 단절과 재연결을 안전하게 관리하는 공통 계층.

현재 PC에는 실제 OPC-DA COM 라이브러리가 없고 KEPServerEX도 아직 구축되지 않았다.
그래서 이 파일은 특정 라이브러리에 묶이지 않는 연결 규칙을 먼저 제공한다. 나중에
실제 백엔드 클래스만 추가하면 주소 매핑과 재연결 로직은 그대로 사용할 수 있다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from typing import Any, Protocol

from communication.plc_io_map import addresses_for


class OpcConnectionState(str, Enum):
    """GUI와 로그에 표시할 OPC 연결 상태."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    RETRY_WAIT = "retry_wait"


class OpcUnavailableError(ConnectionError):
    """OPC 서버가 연결되지 않았거나 명령 도중 끊겼을 때 발생한다."""


class OpcBackend(Protocol):
    """실제 pywin32/OpenOPC 또는 시험용 서버가 구현할 최소 기능."""

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def read(self, item_ids: Sequence[str]) -> Mapping[str, Any]: ...
    def write(self, values: Mapping[str, Any]) -> None: ...


class OpcConnectionManager:
    """통신 실패 후 프로그램을 멈추지 않고 정해진 시간 뒤 재접속한다."""

    def __init__(
        self,
        backend_factory: Callable[[], OpcBackend],
        *,
        retry_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        on_connection_lost: Callable[[str], None] | None = None,
    ) -> None:
        if retry_seconds <= 0:
            raise ValueError("OPC 재접속 간격은 0초보다 커야 합니다.")
        self.backend_factory = backend_factory
        self.retry_seconds = retry_seconds
        self.clock = clock
        self.on_connection_lost = on_connection_lost
        self.backend: OpcBackend | None = None
        self.state = OpcConnectionState.DISCONNECTED
        self.last_error = ""
        self._next_retry_at = 0.0

    def connect_if_due(self) -> bool:
        """최초 연결 또는 재접속 시간이 된 경우에만 서버 연결을 시도한다."""
        if self.state is OpcConnectionState.CONNECTED:
            return True
        if self.clock() < self._next_retry_at:
            return False
        backend = self.backend_factory()
        try:
            backend.connect()
        except Exception as exc:
            self.backend = None
            self._schedule_retry(exc)
            return False
        self.backend = backend
        self.state = OpcConnectionState.CONNECTED
        self.last_error = ""
        return True

    def read(self, item_ids: Sequence[str]) -> dict[str, Any]:
        """연결된 서버에서 값을 읽고 실패하면 즉시 재접속 대기로 전환한다."""
        backend = self._connected_backend()
        try:
            return dict(backend.read(item_ids))
        except Exception as exc:
            self._lose_connection(exc)
            raise OpcUnavailableError(f"OPC 읽기 실패: {exc}") from exc

    def write(self, values: Mapping[str, Any]) -> None:
        """연결된 서버에 값을 쓰고 실패하면 안전하게 단절 상태로 전환한다."""
        backend = self._connected_backend()
        try:
            backend.write(values)
        except Exception as exc:
            self._lose_connection(exc)
            raise OpcUnavailableError(f"OPC 쓰기 실패: {exc}") from exc

    def close(self) -> None:
        """프로그램 종료 시 OPC 연결을 닫는다."""
        if self.backend is not None:
            try:
                self.backend.disconnect()
            finally:
                self.backend = None
        self.state = OpcConnectionState.DISCONNECTED

    def _connected_backend(self) -> OpcBackend:
        if self.state is not OpcConnectionState.CONNECTED or self.backend is None:
            raise OpcUnavailableError("OPC 서버가 연결되어 있지 않습니다.")
        return self.backend

    def _lose_connection(self, error: Exception) -> None:
        if self.backend is not None:
            try:
                self.backend.disconnect()
            except Exception:
                pass
        self.backend = None
        self._schedule_retry(error)
        if self.on_connection_lost is not None:
            self.on_connection_lost(self.last_error)

    def _schedule_retry(self, error: Exception) -> None:
        self.last_error = f"{type(error).__name__}: {error}"
        self.state = OpcConnectionState.RETRY_WAIT
        self._next_retry_at = self.clock() + self.retry_seconds


class PlcAddressOpcAdapter:
    """논리 태그와 현재 확정된 Mitsubishi 주소 사이를 변환한다."""

    def __init__(self, connection: OpcConnectionManager) -> None:
        self.connection = connection

    def read_robot_inputs(self, robot_name: str) -> dict[str, Any]:
        """PLC→Python 주소를 읽어 논리 태그 이름의 사전으로 반환한다."""
        inputs = [
            item for item in addresses_for(robot_name)
            if item.direction == "PLC_TO_PYTHON"
        ]
        values = self.connection.read([item.address for item in inputs])
        missing = [item.address for item in inputs if item.address not in values]
        if missing:
            raise OpcUnavailableError(f"OPC 읽기 결과에 주소가 없습니다: {', '.join(missing)}")
        return {item.logical_tag: values[item.address] for item in inputs}

    def write_robot_outputs(self, robot_name: str, logical_values: Mapping[str, bool]) -> None:
        """Python 응답 태그를 M 주소로 바꿔 OPC 서버에 기록한다."""
        outputs = [
            item for item in addresses_for(robot_name)
            if item.direction == "PYTHON_TO_PLC"
        ]
        required = {item.logical_tag for item in outputs}
        missing = required - set(logical_values)
        if missing:
            raise ValueError(f"누락된 PLC 출력 태그: {', '.join(sorted(missing))}")
        self.connection.write({
            item.address: bool(logical_values[item.logical_tag]) for item in outputs
        })
