"""메인 프로그램과 Dobot Worker가 사용하는 공통 메시지 규칙.

서로 다른 프로세스는 함수 호출을 직접 주고받을 수 없으므로 Queue에 딕셔너리를
넣어 통신한다. 이때 키 이름을 잘못 쓰거나 알 수 없는 명령을 보내면 찾기 어려운
오류가 생길 수 있다. 이 파일은 명령과 응답의 형식을 한곳에서 검사한다.

현재는 읽기 명령, 종료 명령과 제한된 저속 조그 명령만 허용한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProtocolError(ValueError):
    """명령 또는 응답 메시지 형식이 잘못됐을 때 발생하는 오류."""


class CommandName(str, Enum):
    """현재 Worker가 처리하도록 허용된 명령 이름."""

    PING = "ping"
    GET_IDENTITY = "get_identity"
    GET_STATUS = "get_status"
    JOG_START = "jog_start"
    JOG_KEEPALIVE = "jog_keepalive"
    JOG_FOR = "jog_for"
    JOG_STOP = "jog_stop"
    MOVE_PTP = "move_ptp"
    QUEUE_START = "queue_start"
    QUEUE_STOP = "queue_stop"
    QUEUE_CLEAR = "queue_clear"
    GET_QUEUE_INDEX = "get_queue_index"
    SET_END_EFFECTOR = "set_end_effector"
    GET_END_EFFECTOR = "get_end_effector"
    GET_ALARMS = "get_alarms"
    CLEAR_ALARMS = "clear_alarms"
    GET_HOME_PARAMS = "get_home_params"
    SET_HOME_PARAMS = "set_home_params"
    HOME = "home"
    SHUTDOWN = "shutdown"

    @classmethod
    def parse(cls, value: str | "CommandName") -> "CommandName":
        """문자열을 명령으로 변환하며, 목록에 없는 명령은 즉시 거부한다."""
        try:
            return value if isinstance(value, cls) else cls(value)
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(command.value for command in cls)
            raise ProtocolError(f"허용되지 않은 명령 {value!r}; 허용값: {allowed}") from exc


@dataclass(frozen=True)
class CommandMessage:
    """메인 프로세스가 Worker로 보내는 요청."""

    request_id: int
    command: CommandName
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.request_id < 1:
            raise ProtocolError("요청 번호는 1 이상이어야 합니다.")
        if not isinstance(self.payload, dict):
            raise ProtocolError("명령의 payload는 딕셔너리여야 합니다.")

    def to_dict(self) -> dict[str, Any]:
        """multiprocessing Queue에 넣을 수 있는 단순 딕셔너리로 변환한다."""
        return {"id": self.request_id, "command": self.command.value, "payload": self.payload}

    @classmethod
    def from_dict(cls, data: Any) -> "CommandMessage":
        """Queue에서 받은 딕셔너리를 검사하여 명령 객체로 변환한다."""
        if not isinstance(data, dict):
            raise ProtocolError("명령 메시지는 딕셔너리여야 합니다.")
        request_id = data.get("id")
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            raise ProtocolError("명령의 id는 정수여야 합니다.")
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            raise ProtocolError("명령의 payload는 딕셔너리여야 합니다.")
        return cls(
            request_id=request_id,
            command=CommandName.parse(data.get("command")),
            payload=dict(payload),
        )


@dataclass(frozen=True)
class ResponseMessage:
    """Worker가 메인 프로세스로 보내는 명령 처리 결과."""

    request_id: int
    ok: bool
    result: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.request_id,
            "ok": self.ok,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ResponseMessage":
        """응답에 요청 번호와 성공 여부가 올바르게 들어 있는지 검사한다."""
        if not isinstance(data, dict):
            raise ProtocolError("응답 메시지는 딕셔너리여야 합니다.")
        request_id = data.get("id")
        ok = data.get("ok")
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            raise ProtocolError("응답의 id는 정수여야 합니다.")
        if not isinstance(ok, bool):
            raise ProtocolError("응답의 ok는 True 또는 False여야 합니다.")
        error = data.get("error", "")
        if not isinstance(error, str):
            raise ProtocolError("응답의 error는 문자열이어야 합니다.")
        if not ok and not error:
            raise ProtocolError("실패 응답에는 오류 설명이 필요합니다.")
        return cls(request_id, ok, data.get("result"), error)


def success_response(request_id: int, result: Any) -> dict[str, Any]:
    """성공 응답 딕셔너리를 만든다."""
    return ResponseMessage(request_id, True, result=result).to_dict()


def error_response(request_id: int, error: Exception | str) -> dict[str, Any]:
    """예외 또는 문자열로 실패 응답 딕셔너리를 만든다."""
    message = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    return ResponseMessage(request_id, False, error=message).to_dict()
