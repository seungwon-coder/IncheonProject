"""Worker 타임아웃 및 재시작 절차를 실제 로봇 없이 검사한다."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from robot_control.dobot_protocol import CommandName
from robot_control.dobot_worker import (
    HOME_MOTION_TIMEOUT_SECONDS,
    HOME_RESPONSE_MARGIN_SECONDS,
    DobotWorker,
    WorkerRecoveryError,
)
from robot_control.dobot_errors import DobotCommandTimeout


class DobotWorkerRecoveryTest(unittest.TestCase):
    """실패 횟수 제한과 안전한 읽기 명령 복구 규칙을 검사한다."""

    def test_timeout_restarts_worker_and_retries_once(self) -> None:
        """첫 읽기가 시간 초과되면 한 번 재시작한 뒤 결과를 받아야 한다."""
        worker = DobotWorker("Test_Dobot", "COM99")
        expected = {"robot": "Test_Dobot", "pose": {"x": 1.0}}
        worker.request = Mock(side_effect=[TimeoutError("가상 시간 초과"), expected])
        worker.restart = Mock(return_value={"ok": True})

        result = worker.request_with_recovery(CommandName.GET_STATUS, retries=1)

        self.assertEqual(result, expected)
        worker.restart.assert_called_once()
        self.assertEqual(worker.request.call_count, 2)

    def test_command_timeout_is_a_standard_timeout(self) -> None:
        """전용 시간초과도 기존 TimeoutError 처리 코드와 호환되어야 한다."""
        error = DobotCommandTimeout("Dobot_1", "ping", 1.0)
        self.assertIsInstance(error, TimeoutError)

    def test_new_worker_reports_not_running(self) -> None:
        """연결 전이나 시간초과로 종료된 Worker는 GUI에 미실행으로 보여야 한다."""
        worker = DobotWorker("Test_Dobot", "COM99")
        self.assertFalse(worker.is_running)

    def test_home_timeout_has_response_margin(self) -> None:
        """기계적 HOME 완료 제한과 프로세스 응답 제한 사이에 여유가 있어야 한다."""
        self.assertEqual(HOME_MOTION_TIMEOUT_SECONDS, 60.0)
        self.assertGreater(HOME_RESPONSE_MARGIN_SECONDS, 0.0)

    def test_recovery_stops_after_retry_limit(self) -> None:
        """계속 실패해도 무한 반복하지 않고 정해진 횟수에서 끝나야 한다."""
        worker = DobotWorker("Test_Dobot", "COM99")
        worker.request = Mock(side_effect=TimeoutError("계속되는 가상 시간 초과"))
        worker.restart = Mock(return_value={"ok": True})

        with self.assertRaises(WorkerRecoveryError):
            worker.request_with_recovery(CommandName.GET_STATUS, retries=1)

        worker.restart.assert_called_once()
        self.assertEqual(worker.request.call_count, 2)

    def test_shutdown_is_never_automatically_retried(self) -> None:
        """종료처럼 읽기 전용이 아닌 명령은 자동 재시도 대상이 아니어야 한다."""
        worker = DobotWorker("Test_Dobot", "COM99")

        with self.assertRaises(ValueError):
            worker.request_with_recovery(CommandName.SHUTDOWN)

    def test_jog_start_is_never_automatically_retried(self) -> None:
        """조그 시작은 통신 오류가 나도 중복 실행하지 않아야 한다."""
        worker = DobotWorker("Test_Dobot", "COM99")

        with self.assertRaises(ValueError):
            worker.request_with_recovery(CommandName.JOG_START)

    def test_ptp_is_never_automatically_retried(self) -> None:
        """PTP 이동도 통신 오류 시 중복 실행해서는 안 된다."""
        worker = DobotWorker("Test_Dobot", "COM99")
        with self.assertRaises(ValueError):
            worker.request_with_recovery(CommandName.MOVE_PTP)

    def test_queue_mutation_is_never_automatically_retried(self) -> None:
        """큐 시작·정지·초기화는 응답 유실 시 자동 중복 전송하지 않는다."""
        worker = DobotWorker("Test_Dobot", "COM99")
        for command in (
            CommandName.QUEUE_START,
            CommandName.QUEUE_STOP,
            CommandName.QUEUE_CLEAR,
        ):
            with self.assertRaises(ValueError):
                worker.request_with_recovery(command)

    def test_end_effector_set_is_never_automatically_retried(self) -> None:
        """출력 명령은 응답 유실 시 자동 중복 전송하지 않는다."""
        worker = DobotWorker("Test_Dobot", "COM99")
        with self.assertRaises(ValueError):
            worker.request_with_recovery(CommandName.SET_END_EFFECTOR)

    def test_alarm_clear_is_never_automatically_retried(self) -> None:
        """알람 초기화는 상태 변경 명령이므로 자동 중복 전송하지 않는다."""
        worker = DobotWorker("Test_Dobot", "COM99")
        with self.assertRaises(ValueError):
            worker.request_with_recovery(CommandName.CLEAR_ALARMS)

    def test_home_is_never_automatically_retried(self) -> None:
        """HOME 이동은 통신 오류가 나도 자동 중복 실행하지 않는다."""
        worker = DobotWorker("Test_Dobot", "COM99")
        with self.assertRaises(ValueError):
            worker.request_with_recovery(CommandName.HOME)

    def test_home_parameter_set_is_never_automatically_retried(self) -> None:
        """HOME 좌표 저장은 상태 변경 명령이므로 자동 중복 실행하지 않는다."""
        worker = DobotWorker("Dobot_1", "COM99")
        with self.assertRaises(ValueError):
            worker.request_with_recovery(CommandName.SET_HOME_PARAMS)

    def test_home_parameter_change_is_limited_to_dobot_1_and_2(self) -> None:
        """사용자가 지정하지 않은 Dobot_3·4의 HOME 값 변경을 사전에 차단한다."""
        worker = DobotWorker("Dobot_3", "COM99")
        with self.assertRaises(ValueError):
            worker.set_home_params({"x": 1, "y": 2, "z": 3, "r": 4})

    def test_negative_retry_count_is_rejected(self) -> None:
        """잘못된 음수 재시도 횟수는 실행 전에 거부해야 한다."""
        worker = DobotWorker("Test_Dobot", "COM99")

        with self.assertRaises(ValueError):
            worker.request_with_recovery(CommandName.PING, retries=-1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
