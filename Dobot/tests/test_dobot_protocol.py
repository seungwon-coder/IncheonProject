"""Worker 명령·응답 프로토콜을 실제 로봇 없이 검사한다."""

from __future__ import annotations

import unittest

from robot_control.dobot_protocol import (
    CommandMessage,
    CommandName,
    ProtocolError,
    ResponseMessage,
    error_response,
    success_response,
)


class DobotProtocolTest(unittest.TestCase):
    """정상 메시지는 통과하고 위험하거나 잘못된 메시지는 차단되는지 검사한다."""

    def test_command_round_trip(self) -> None:
        """명령을 딕셔너리로 바꿨다가 다시 읽어도 값이 같아야 한다."""
        original = CommandMessage(1, CommandName.GET_STATUS)
        restored = CommandMessage.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_unknown_command_is_rejected(self) -> None:
        """등록되지 않은 이동 명령은 프로토콜 단계에서 거부해야 한다."""
        with self.assertRaises(ProtocolError):
            CommandName.parse("move_without_permission")

    def test_invalid_request_id_is_rejected(self) -> None:
        """응답을 구분할 수 없는 잘못된 요청 번호는 거부해야 한다."""
        with self.assertRaises(ProtocolError):
            CommandMessage.from_dict({"id": 0, "command": "ping"})

    def test_command_payload_round_trip(self) -> None:
        """조그 조건도 프로세스 사이에서 손실 없이 전달되어야 한다."""
        original = CommandMessage(
            2,
            CommandName.JOG_FOR,
            {"axis": "x", "direction": "+", "duration": 0.1, "speed_percent": 5},
        )
        self.assertEqual(CommandMessage.from_dict(original.to_dict()), original)

    def test_non_dictionary_payload_is_rejected(self) -> None:
        """예상하지 않은 자료형은 Worker에 들어가기 전에 거부해야 한다."""
        with self.assertRaises(ProtocolError):
            CommandMessage.from_dict({"id": 2, "command": "jog_for", "payload": []})

    def test_immediate_jog_commands_are_registered(self) -> None:
        """별도의 시작·정지 명령 이름을 오타 없이 읽을 수 있어야 한다."""
        self.assertIs(CommandName.parse("jog_start"), CommandName.JOG_START)
        self.assertIs(CommandName.parse("jog_stop"), CommandName.JOG_STOP)
        self.assertIs(CommandName.parse("jog_keepalive"), CommandName.JOG_KEEPALIVE)

    def test_queue_commands_are_registered(self) -> None:
        """큐 제어 명령 네 종류가 공통 프로토콜에 등록되어야 한다."""
        self.assertIs(CommandName.parse("queue_start"), CommandName.QUEUE_START)
        self.assertIs(CommandName.parse("queue_stop"), CommandName.QUEUE_STOP)
        self.assertIs(CommandName.parse("queue_clear"), CommandName.QUEUE_CLEAR)
        self.assertIs(CommandName.parse("get_queue_index"), CommandName.GET_QUEUE_INDEX)

    def test_alarm_commands_are_registered(self) -> None:
        """알람 조회와 초기화 명령이 공통 프로토콜에 등록되어야 한다."""
        self.assertIs(CommandName.parse("get_alarms"), CommandName.GET_ALARMS)
        self.assertIs(CommandName.parse("clear_alarms"), CommandName.CLEAR_ALARMS)

    def test_home_command_is_registered(self) -> None:
        """기계적 HOME 명령이 공통 프로토콜에 등록되어야 한다."""
        self.assertIs(CommandName.parse("home"), CommandName.HOME)

    def test_home_parameter_commands_are_registered(self) -> None:
        """HOME 복귀 위치 조회와 저장 명령이 프로토콜에 등록되어야 한다."""
        self.assertIs(
            CommandName.parse("get_home_params"), CommandName.GET_HOME_PARAMS
        )
        self.assertIs(
            CommandName.parse("set_home_params"), CommandName.SET_HOME_PARAMS
        )

    def test_success_response_round_trip(self) -> None:
        """성공 결과가 손실 없이 전달되는지 확인한다."""
        raw = success_response(7, {"state": "ready"})
        response = ResponseMessage.from_dict(raw)
        self.assertTrue(response.ok)
        self.assertEqual(response.result, {"state": "ready"})

    def test_error_response_requires_message(self) -> None:
        """실패 응답에는 사람이 확인할 오류 설명이 반드시 있어야 한다."""
        response = ResponseMessage.from_dict(error_response(8, "통신 실패"))
        self.assertFalse(response.ok)
        self.assertEqual(response.error, "통신 실패")

        with self.assertRaises(ProtocolError):
            ResponseMessage.from_dict({"id": 8, "ok": False, "error": ""})


if __name__ == "__main__":
    unittest.main(verbosity=2)
