"""Construct the GUI without connecting to PLC or robot hardware."""
import gc
import unittest
from unittest.mock import Mock, patch

from integrated_gui import IntegratedDobotGui


class GuiOperationModesTest(unittest.TestCase):
    def setUp(self):
        self.gui = IntegratedDobotGui()
        self.gui.withdraw()

    def tearDown(self):
        # Tk 객체를 self.gui에 남겨 두면 다음 asyncua 테스트의 백그라운드
        # 스레드가 가비지 수집을 수행하면서 Tcl_AsyncDelete가 발생할 수 있다.
        # GUI를 만든 메인 스레드에서 참조 제거와 수집까지 끝낸다.
        self.gui.destroy()
        self.gui = None
        gc.collect()

    def test_default_manual_and_auto_disables_manual_controls(self):
        self.assertIn("수동", self.gui.mode_text.get())
        operation = Mock()
        operation.events = __import__("queue").Queue()
        operation.active = True
        self.gui.automatic = operation
        self.gui.automatic_mode_enabled = True
        self.gui._poll_automatic()
        self.assertTrue(self.gui.manual_controls)
        self.assertTrue(all(str(widget.cget("state")) == "disabled"
                            for widget, _ in self.gui.manual_controls))
        self.assertNotEqual(str(self.gui.main_stock_spinbox.cget("state")), "disabled")
        self.assertNotEqual(str(self.gui.main_stock_button.cget("state")), "disabled")
        self.gui._manual_mode()
        operation.set_automatic.assert_called_with(False)
        operation.active = False
        self.gui._poll_automatic()
        self.assertIsNone(self.gui.automatic)
        for widget, state in self.gui.manual_controls:
            self.assertEqual(str(widget.cget("state")), state)

    @patch("integrated_gui.integrated_gui.messagebox.showinfo")
    def test_auto_requires_live_connection(self, showinfo):
        self.gui._automatic_mode()
        self.assertIsNone(self.gui.automatic)
        showinfo.assert_called_once()

    @patch("integrated_gui.integrated_gui.IntegratedDobotGui._confirm_actual_motion")
    @patch("integrated_gui.integrated_gui.IntegratedDobotGui._run_live_background")
    def test_manual_dobot_2_cycle_uses_selected_assembly(
        self, run_background, confirm_motion
    ):
        self.gui.selected_robot.set("Dobot_2")
        self.gui.start_signal.set(True)
        self.gui.live_process = Mock()
        confirm_motion.return_value = "Dobot_2"

        for assembly, completed, label in (
            (1, 0, "첫 번째 조립"), (2, 1, "두 번째 조립")
        ):
            with self.subTest(assembly=assembly):
                self.gui.manual_assembly.set(assembly)
                self.gui._actual_cycle()
                self.assertIn(label, confirm_motion.call_args.args[0])
                result = run_background.call_args.args[0]()
                self.assertEqual(result[0:2], ("actual_cycle", "Dobot_2"))
                signals = self.gui.live_process.process.call_args.args[1]
                self.assertEqual(signals.completed_count, completed)
                self.assertEqual(signals.work_count, assembly)

    def test_manual_assembly_selection_does_not_change_other_robots(self):
        self.gui.manual_assembly.set(2)

        signals = self.gui._signal_values("Dobot_1")

        self.assertEqual(signals["work_count"], 1)
        self.assertIsNone(signals["completed_count"])

    def test_main_inventory_confirmation_uses_safe_manager_api(self):
        self.gui.live_process = Mock()
        self.gui.live_process.main_inventory.count = 2
        self.gui.live_process.main_inventory.status_text = "사용 가능"
        self.gui.main_stock.set(2)

        self.gui._confirm_main_stock()

        self.gui.live_process.set_main_inventory_count.assert_called_once_with(2)
        self.assertTrue(self.gui.main_stock_confirmed)

    def test_inventory_refresh_preserves_operator_input(self):
        self.gui.live_process = Mock()
        self.gui.live_process.main_inventory.count = 1
        self.gui.live_process.main_inventory.status_text = "사용 가능"
        self.gui.main_stock_confirmed = True
        self.gui._main_stock_synced_count = 1
        self.gui.main_stock.set(3)

        self.gui._refresh_main_stock()

        self.assertEqual(self.gui.main_stock.get(), 3)
        self.assertEqual(self.gui._main_stock_synced_count, 1)

    def test_inventory_refresh_updates_unedited_value_after_cycle(self):
        self.gui.live_process = Mock()
        self.gui.live_process.main_inventory.count = 2
        self.gui.live_process.main_inventory.status_text = "사용 가능"
        self.gui.main_stock_confirmed = True
        self.gui._main_stock_synced_count = 1
        self.gui.main_stock.set(1)

        self.gui._refresh_main_stock()

        self.assertEqual(self.gui.main_stock.get(), 2)
        self.assertEqual(self.gui._main_stock_synced_count, 2)

    @patch("integrated_gui.integrated_gui.IntegratedDobotGui._run_live_background")
    def test_opc_reconnect_uses_existing_robot_connection(self, run_background):
        self.gui.live_service = Mock(connected=True)
        self.gui.live_process = Mock()
        self.gui.live_process.restore_plc_ready_after_reconnect.return_value = {
            "Dobot_1": "READY 좌표 확인 완료"
        }

        self.gui._connect_opc()

        work = run_background.call_args.args[0]
        self.assertEqual(work()[0], "opc_connect")
        self.gui.live_process.restore_plc_ready_after_reconnect.assert_called_once()
        self.gui.live_service.connect_all.assert_not_called()

    @patch("integrated_gui.integrated_gui.IntegratedDobotGui._run_live_background")
    def test_opc_disconnect_keeps_robot_connection(self, run_background):
        operation = Mock(active=True)
        operation.thread.is_alive.return_value = False
        self.gui.automatic = operation
        self.gui.live_service = Mock(connected=True)

        self.gui._disconnect_opc()

        work = run_background.call_args.args[0]
        self.assertEqual(work(), ("opc_close",))
        operation.stop.assert_called_once()
        self.gui.live_service.close_all.assert_not_called()

    @patch("integrated_gui.integrated_gui.IntegratedDobotGui._run_live_background")
    def test_ready_button_approves_selected_robot_without_motion(self, run_background):
        self.gui.selected_robot.set("Dobot_3")
        self.gui.live_service = Mock(connected=True)
        self.gui.live_process = Mock()
        self.gui.automatic = Mock()
        self.gui.automatic.connected.is_set.return_value = True

        self.gui._approve_selected_ready()

        work = run_background.call_args.args[0]
        self.assertEqual(work()[:2], ("approve_ready", "Dobot_3"))
        self.gui.live_process.approve_ready.assert_called_once_with("Dobot_3")
        self.gui.live_process.initialize.assert_not_called()

    def test_plc_output_signals_are_visible_for_each_robot(self):
        """실제 핸드셰이크 값을 로봇별 ON/OFF 표시로 확인할 수 있어야 한다."""
        response = Mock(running=False, finish=False, error=False, ready=True)
        live_process = Mock()
        live_process.state.return_value = "waiting"
        live_process.plc_response.return_value = response
        self.gui.live_process = live_process
        exchange = Mock()
        exchange.latest_start = {name: False for name in self.gui.plc_signal_labels}
        exchange.latest_dobot_4_base_start = True
        exchange.latest_start["Dobot_1"] = True
        self.gui.automatic = exchange

        self.gui._refresh()

        for robot_name in self.gui.plc_signal_labels:
            labels = self.gui.plc_signal_labels[robot_name]
            self.assertEqual(labels["RUNNING"].cget("text"), "RUNNING OFF")
            self.assertEqual(labels["FINISH"].cget("text"), "FINISH OFF")
            self.assertEqual(labels["ERROR"].cget("text"), "ERROR OFF")
            self.assertEqual(labels["READY"].cget("text"), "READY ON")
        self.assertEqual(
            self.gui.plc_signal_labels["Dobot_1"]["START"].cget("text"),
            "START ON",
        )
        self.assertEqual(
            self.gui.plc_signal_labels["Dobot_4"]["BASE START"].cget("text"),
            "BASE START ON",
        )
