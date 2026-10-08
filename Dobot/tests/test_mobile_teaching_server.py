"""실제 Dobot과 네트워크 없이 모바일 상태 화면의 자료 변환을 검사한다."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from robot_control.dobot_config import ROBOT_NAMES
from integrated_gui.integrated_robot_status import RobotLiveStatus
from teaching.mobile_teaching_server import (
    ControlLease,
    MobileStatusApplication,
    PAGE_HTML,
    status_snapshot,
)
from teaching.teaching_points import load_teaching_data, new_teaching_data, save_teaching_data


class MobileTeachingServerTests(unittest.TestCase):
    def test_snapshot_contains_all_robots_and_is_read_only(self) -> None:
        statuses = {
            name: RobotLiveStatus(
                connected=True,
                pose={"x": 1.0, "y": 2.0, "z": 3.0, "r": 4.0},
                joints=[1.0, 2.0, 3.0, 4.0],
                alarms=[],
            )
            for name in ROBOT_NAMES
        }

        result = status_snapshot(statuses)

        self.assertFalse(result["read_only"])
        self.assertEqual(tuple(result["robots"]), ROBOT_NAMES)
        self.assertEqual(result["robots"]["Dobot_4"]["pose"]["r"], 4.0)

    def test_page_has_status_and_watchdog_jog_but_no_ptp_move(self) -> None:
        """JOG는 0.25초 감시를 사용하고 PTP 이동은 아직 없어야 한다."""
        self.assertIn("/api/status", PAGE_HTML)
        self.assertIn("/api/jog/start", PAGE_HTML)
        self.assertIn("/api/jog/keepalive", PAGE_HTML)
        self.assertIn("/api/jog/stop", PAGE_HTML)
        self.assertIn("}, 100)", PAGE_HTML)
        self.assertNotIn("/api/move", PAGE_HTML)

    def test_page_has_point_list_and_current_position_save(self) -> None:
        self.assertIn("/api/points", PAGE_HTML)
        self.assertIn("/api/point/save", PAGE_HTML)
        self.assertIn("현재 위치 저장/수정", PAGE_HTML)

    def test_save_point_uses_current_pose_without_motion(self) -> None:
        class Worker:
            def get_status(self):
                return {
                    "pose": {"x": 11.0, "y": 22.0, "z": 33.0, "r": 44.0},
                    "alarms": [],
                }

        workers = {name: Worker() for name in ROBOT_NAMES}
        service = SimpleNamespace(fleet=SimpleNamespace(workers=workers))
        with TemporaryDirectory() as folder:
            path = Path(folder) / "teaching_points.json"
            save_teaching_data(new_teaching_data(), path)
            app = MobileStatusApplication(service, path)

            result = app.save_point("Dobot_2", "safe_point", 35)

            self.assertEqual(result["point"], "safe_point")
            saved = load_teaching_data(path)
            point = saved["robots"]["Dobot_2"]["safe_point"]
            self.assertTrue(point["valid"])
            self.assertEqual(point["pose"]["z"], 33.0)
            self.assertEqual(point["speed_percent"], 35.0)

    def test_alarm_blocks_mobile_point_save(self) -> None:
        worker = SimpleNamespace(get_status=lambda: {
            "pose": {"x": 1.0, "y": 2.0, "z": 3.0, "r": 4.0},
            "alarms": [17],
        })
        service = SimpleNamespace(
            fleet=SimpleNamespace(workers={name: worker for name in ROBOT_NAMES})
        )
        with TemporaryDirectory() as folder:
            path = Path(folder) / "teaching_points.json"
            save_teaching_data(new_teaching_data(), path)
            app = MobileStatusApplication(service, path)
            with self.assertRaises(RuntimeError):
                app.save_point("Dobot_1", "ready", 5)

    def test_mobile_jog_buttons_disable_text_selection(self) -> None:
        """길게 누른 JOG 버튼이 텍스트 선택 대상으로 바뀌면 안 된다."""
        self.assertIn("-webkit-user-select: none", PAGE_HTML)
        self.assertIn("-webkit-touch-callout: none", PAGE_HTML)
        self.assertIn("selectstart", PAGE_HTML)
        self.assertIn("button.type = 'button'", PAGE_HTML)

    def test_mobile_page_displays_server_error_message(self) -> None:
        """장비 오류가 생기면 모호한 문구 대신 서버가 보낸 원인을 표시한다."""
        self.assertIn("result.error || `HTTP ${response.status}`", PAGE_HTML)

    def test_page_has_client_id_fallback_for_plain_http_mobile_browser(self) -> None:
        """일반 HTTP에서 randomUUID가 없어도 화면 JavaScript가 중단되면 안 된다."""
        self.assertIn("typeof globalThis.crypto.randomUUID", PAGE_HTML)
        self.assertIn("Math.random().toString(36)", PAGE_HTML)

    def test_one_controller_locks_one_robot(self) -> None:
        lease = ControlLease(timeout_seconds=15.0)
        first = lease.acquire("phone-a", "Dobot_1")

        self.assertTrue(first["active"])
        self.assertEqual(first["robot"], "Dobot_1")
        with self.assertRaises(RuntimeError):
            lease.acquire("phone-b", "Dobot_2")

        released = lease.release(first["token"])
        self.assertFalse(released["active"])

    def test_control_expires_when_heartbeat_stops(self) -> None:
        now = [100.0]
        lease = ControlLease(timeout_seconds=15.0, clock=lambda: now[0])
        first = lease.acquire("phone-a", "Dobot_3")
        now[0] += 16.0

        second = lease.acquire("phone-b", "Dobot_4")

        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual(second["robot"], "Dobot_4")

    def test_wrong_token_cannot_refresh_or_release_control(self) -> None:
        lease = ControlLease()
        lease.acquire("phone-a", "Dobot_1")

        with self.assertRaises(PermissionError):
            lease.heartbeat("wrong-token")
        with self.assertRaises(PermissionError):
            lease.release("wrong-token")

    def test_token_only_authorizes_its_locked_robot(self) -> None:
        lease = ControlLease()
        acquired = lease.acquire("phone-a", "Dobot_3")
        self.assertEqual(lease.robot_for(acquired["token"]), "Dobot_3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
