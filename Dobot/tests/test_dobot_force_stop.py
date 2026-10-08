"""강제 즉시 정지의 SDK 호출 순서를 실제 Dobot 없이 검사한다."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from robot_control.dobot_worker import _apply_force_stop


class FakeJointCommand:
    """SDK의 JOG 정지 번호만 제공하는 간단한 가짜 자료이다."""

    JogIdle = 0


class DobotForceStopTest(unittest.TestCase):
    def test_force_stop_stops_jog_and_queue_then_acknowledges(self) -> None:
        """요청 시 JOG 정지, 큐 강제 정지, 큐 삭제가 모두 실행되어야 한다."""
        sdk = Mock()
        sdk.JC = FakeJointCommand
        requested = Mock()
        requested.is_set.return_value = True
        applied = Mock()

        result = _apply_force_stop(sdk, "fake_api", requested, applied)

        self.assertTrue(result)
        requested.clear.assert_called_once_with()
        sdk.SetJOGCmd.assert_called_once_with("fake_api", 0, 0, 0)
        sdk.SetQueuedCmdForceStopExec.assert_called_once_with("fake_api")
        sdk.SetQueuedCmdClear.assert_called_once_with("fake_api")
        applied.set.assert_called_once_with()

    def test_no_request_does_not_send_sdk_command(self) -> None:
        """정지 요청이 없으면 로봇 SDK에는 아무 명령도 보내지 않는다."""
        sdk = Mock()
        requested = Mock()
        requested.is_set.return_value = False
        applied = Mock()

        result = _apply_force_stop(sdk, "fake_api", requested, applied)

        self.assertFalse(result)
        sdk.SetJOGCmd.assert_not_called()
        sdk.SetQueuedCmdForceStopExec.assert_not_called()
        applied.set.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
