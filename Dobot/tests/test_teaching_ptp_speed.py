"""수동 PTP 입력이 장비 명령에 전달되는지 장비 없이 검사한다."""
import unittest
from unittest.mock import Mock, patch

from teaching.teaching_gui import TeachingGUI


class TeachingPTPSpeedTest(unittest.TestCase):
    def setUp(self):
        self.gui = TeachingGUI.__new__(TeachingGUI)
        self.gui.robot_var = Mock(get=lambda: "Dobot_1")
        self.gui.point_var = Mock(get=lambda: "ready")
        self.gui.ptp_speed_var = Mock(get=lambda: "25")
        self.gui.status_var = Mock()
        self.point = {"valid": True, "pose": dict(x=100, y=0, z=50, r=0),
                      "speed_percent": 60}
        self.gui.points = {"robots": {"Dobot_1": {"ready": self.point}}}
        self.worker = Mock()
        self.gui._connected = Mock(return_value=self.worker)
        self.gui._background = Mock(side_effect=lambda work, done: work())

    @patch("teaching.teaching_gui.messagebox.askyesno", return_value=True)
    def test_selected_speed_reaches_worker_without_changing_saved_point(self, confirm):
        self.gui.move_to_point()
        self.assertEqual(self.worker.move_ptp.call_args.kwargs["speed_percent"], 25)
        self.assertEqual(self.point["speed_percent"], 60)
        self.assertIn("25.0%", confirm.call_args.args[1])

    @patch("teaching.teaching_gui.messagebox.showwarning")
    @patch("teaching.teaching_gui.messagebox.askyesno")
    def test_invalid_speed_never_sends_motion(self, confirm, warning):
        for value in ("", "abc", "0", "4.9", "100.1", "nan", "inf"):
            with self.subTest(value=value):
                self.gui.ptp_speed_var.get = lambda: value
                self.gui.move_to_point()
        self.worker.move_ptp.assert_not_called()
        confirm.assert_not_called()
        self.assertEqual(warning.call_count, 7)

    @patch("teaching.teaching_gui.messagebox.askyesno", return_value=True)
    def test_fractional_speed_is_not_rounded_up(self, confirm):
        for speed in (5.0, 100.0):
            with self.subTest(speed=speed):
                self.gui.ptp_speed_var.get = lambda: str(speed)
                self.gui.move_to_point()
                self.assertEqual(self.worker.move_ptp.call_args.kwargs["speed_percent"], speed)

    @patch("teaching.teaching_gui.messagebox.askyesno", return_value=False)
    def test_cancel_does_not_move(self, confirm):
        self.gui.move_to_point()
        self.worker.move_ptp.assert_not_called()
