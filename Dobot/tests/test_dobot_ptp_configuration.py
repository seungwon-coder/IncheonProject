"""PTP 속도 설정 순서와 장비 피드백 검사를 실제 로봇 없이 확인한다."""
import unittest
import time
from unittest.mock import Mock, patch, call

from robot_control.dobot_worker import _configure_ptp_motion, _execute_ptp_segment


class DobotPTPConfigurationTest(unittest.TestCase):
    def test_speed_precedes_motion_in_same_queue(self):
        sdk = Mock()
        sdk.SetPTPCommonParams.return_value = [41]
        sdk.SetPTPCmd.return_value = [42]
        sdk.GetQueuedCmdCurrentIndex.return_value = [42]
        sdk.PTPMode.PTPMOVJXYZMode = 1
        target = dict(x=100, y=0, z=50, r=0)
        with patch('robot_control.dobot_worker._pose', return_value=target), \
             patch('robot_control.dobot_worker._alarm_codes', return_value=[]), \
             patch('robot_control.dobot_worker._apply_force_stop', return_value=False), \
             patch('robot_control.dobot_worker.time.sleep'):
            index = _configure_ptp_motion(sdk, 'api', 7)
            after, _, _ = _execute_ptp_segment(
                sdk, 'api', target, 0.25, time.monotonic() + 10,
                Mock(), Mock(), speed_queue_index=index)
        self.assertEqual(after, target)
        self.assertEqual(sdk.mock_calls[:3], [
            call.SetPTPCommonParams('api', 7, 10, 1),
            call.SetPTPCmd('api', 1, 100, 0, 50, 0, 1),
            call.SetQueuedCmdStartExec('api'),
        ])

    def test_reversed_queue_order_never_starts_motion(self):
        sdk = Mock()
        sdk.SetPTPCmd.return_value = [40]
        target = dict(x=100, y=0, z=50, r=0)
        with patch('robot_control.dobot_worker._pose', return_value=target):
            with self.assertRaisesRegex(RuntimeError, '큐 순서'):
                _execute_ptp_segment(sdk, 'api', target, 0.25,
                                     time.monotonic() + 10, Mock(), Mock(),
                                     speed_queue_index=41)
        sdk.SetQueuedCmdStartExec.assert_not_called()
        sdk.SetQueuedCmdClear.assert_called_once_with('api')

    def test_supported_ratio_is_applied_directly(self) -> None:
        sdk = Mock()
        sdk.SetPTPCommonParams.return_value = [41]

        applied = _configure_ptp_motion(sdk, "api", 25.0)

        self.assertEqual(applied, 41)
        sdk.SetPTPCommonParams.assert_called_once_with("api", 25.0, 10, 1)
        sdk.GetPTPCommonParams.assert_not_called()
        sdk.SetPTPJointParams.assert_not_called()
        sdk.SetPTPCoordinateParams.assert_not_called()

    def test_old_readback_does_not_block_speed_registration(self) -> None:
        sdk = Mock()
        sdk.GetPTPCommonParams.return_value = [20.0, 10.0]
        sdk.SetPTPCommonParams.return_value = [42]
        self.assertEqual(_configure_ptp_motion(sdk, "api", 25.0), 42)
        sdk.GetPTPCommonParams.assert_not_called()

    def test_missing_readback_is_rejected(self) -> None:
        sdk = Mock()
        sdk.SetPTPCommonParams.return_value = []
        with self.assertRaisesRegex(RuntimeError, "큐 등록 응답 오류"):
            _configure_ptp_motion(sdk, "api", 5.0)


if __name__ == "__main__":
    unittest.main()
