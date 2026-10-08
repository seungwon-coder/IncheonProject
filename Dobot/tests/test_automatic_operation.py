import unittest
import threading
import time
from unittest.mock import Mock

from communication.automatic_operation import AutomaticOperation, StartEdges, cycle_signals
from communication.plc_handshake import RobotPlcHandshake, RobotResponseSignals
from communication.robot_opcua_tags import node_map_for_robot
from robot_control.dobot_config import ROBOT_NAMES
from robot_control.process_state_machine import RobotProcessStateMachine, ProcessState


class AutomaticOperationTest(unittest.TestCase):
    @staticmethod
    def wait_until(predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_entry_high_and_held_high_do_not_trigger(self):
        edges = StartEdges()
        self.assertFalse(edges.consume("Dobot_1", True))
        self.assertFalse(edges.consume("Dobot_1", False))
        self.assertTrue(edges.consume("Dobot_1", True))
        self.assertFalse(edges.consume("Dobot_1", True))
        with self.assertRaises(ValueError):
            edges.consume("Dobot_1", None)

    def test_plc_counter_selects_destination_independent_of_manual_history(self):
        controller = Mock()
        machine = RobotProcessStateMachine("Dobot_2", controller)
        for finished, expected in ((1, "assembly_2"), (0, "assembly_1")):
            machine.state = ProcessState.WAITING
            signals = cycle_signals("Dobot_2", {
                "input.program_no": 99, "input.target_count": 2,
                "counter.finish_count": finished}, "Main")
            machine.process(signals)
            controller.run_cycle.assert_called_with(expected)

    def test_invalid_counter_combinations_rejected(self):
        for target, finished in ((0, 0), (2, 2), (1, -1), (3, 0),
                                 (True, 0), (1, 0.5)):
            with self.assertRaises(ValueError):
                cycle_signals("Dobot_2", {
                    "input.target_count": target, "counter.finish_count": finished
                }, "Main")

    def test_dobot_2_uses_target_count_without_program_number(self):
        for target, finished in ((1, 0), (2, 0), (2, 1)):
            with self.subTest(target=target, finished=finished):
                signals = cycle_signals("Dobot_2", {
                    "input.target_count": target,
                    "counter.finish_count": finished,
                })
                self.assertEqual(signals.work_count, target)
                self.assertEqual(signals.completed_count, finished)

    def test_dobot_2_first_of_two_does_not_reserve_second_assembly(self):
        operation = AutomaticOperation(Mock(), "Main")
        active = cycle_signals("Dobot_2", {
            "input.target_count": 2, "counter.finish_count": 0
        })

        pending = operation._reserve_start(
            Mock(), "Dobot_2", node_map_for_robot("Dobot_2"), active
        )

        self.assertIsNone(pending)

    def test_next_dobot_2_product_requires_observed_counter_reset(self):
        mapping = node_map_for_robot("Dobot_2")
        backend = Mock()
        backend.read.return_value = {
            mapping["input.target_count"]: 1,
            mapping["counter.finish_count"]: 0,
        }
        operation = AutomaticOperation(Mock(), "Main")
        pending = operation._reserve_start(
            backend, "Dobot_2", mapping,
            cycle_signals("Dobot_2", {
                "input.target_count": 2, "counter.finish_count": 1
            }),
        )

        self.assertIsNone(operation._pending_signals(
            backend, "Dobot_2", mapping, pending, reset_seen=False
        ))
        self.assertEqual(operation._pending_signals(
            backend, "Dobot_2", mapping, pending, reset_seen=True
        ).completed_count, 0)

    def test_dobot_4_vision_codes_select_product(self):
        """비전 코드가 상태 머신에서 사용할 제품명으로 정확히 변환된다."""
        expected = {
            2: ("Lamp_a", "return_Lamp_a"),
            3: ("Lamp_b", "return_Lamp_b"),
            4: ("Seat_a", "return_Seat_a"),
            5: ("Seat_b", "return_Seat_b"),
            6: ("Main", "storage_base_1st"),
        }
        for code, (product_type, destination) in expected.items():
            signals = cycle_signals(
                "Dobot_4", {"input.product_type": code}, "무시되는 GUI 값"
            )
            self.assertTrue(signals.start)
            self.assertEqual(signals.product_type, product_type)

            # 변환된 제품명이 최종 반환 포인트까지 정확히 이어지는지 확인한다.
            controller = Mock()
            machine = RobotProcessStateMachine("Dobot_4", controller)
            machine.state = ProcessState.WAITING
            machine.process(signals)
            controller.run_cycle.assert_called_once_with(destination)

    def test_dobot_4_invalid_vision_code_blocks_cycle(self):
        for invalid in (0, 1, 7, 2.0, True, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                cycle_signals("Dobot_4", {"input.product_type": invalid})

    def test_queued_dobot_4_captures_vision_at_second_start(self):
        mapping = node_map_for_robot("Dobot_4")
        backend = Mock()
        backend.read.return_value = {mapping["input.product_type"]: 3}
        operation = AutomaticOperation(Mock(), "Main")

        pending = operation._reserve_start(
            backend, "Dobot_4", mapping, cycle_signals(
                "Dobot_4", {"input.product_type": 2}
            )
        )

        self.assertEqual(pending.signals.product_type, "Lamp_b")
        backend.read.assert_called_once_with([mapping["input.product_type"]])

    def test_dobot_4_vision_node_is_exact_confirmed_node_id(self):
        from communication.robot_opcua_tags import node_map_for_robot

        self.assertEqual(
            node_map_for_robot("Dobot_4")["input.product_type"],
            "ns=2;s=M.PLC.VISION_I/O.VISION4_CLASS_RESULT",
        )
        mapping = node_map_for_robot("Dobot_4")
        self.assertEqual(
            mapping["command.base_start"],
            "ns=2;s=M.PLC.ROBOT_I/O.ROBOT_START(B)_(4)",
        )
        self.assertEqual(
            mapping["status.base_done"],
            "ns=2;s=M.PLC.ROBOT_I/O.ROBOT_FINISH(B)_(4)",
        )

    def test_stopping_publishes_not_ready_and_never_writes_counters(self):
        manager = Mock()
        manager.plc_response.return_value = RobotResponseSignals(finish=True, ready=True)
        operation = AutomaticOperation(manager, "Main")
        operation.stop()
        backend = Mock()
        operation._publish(backend, "Dobot_2")
        values = backend.write.call_args.args[0]
        self.assertEqual(len(values), 4)
        self.assertFalse(values["ns=2;s=M.PLC.ROBOT_I/O.ROBOT_READY_(2)"])
        self.assertTrue(values["ns=2;s=M.PLC.ROBOT_I/O.ROBOT_FINISH_(2)"])
        manager.mark_finish_published.assert_called_once_with("Dobot_2")

    def test_finish_pulse_starts_only_after_opc_write_succeeds(self):
        manager = Mock()
        manager.plc_response.return_value = RobotResponseSignals(finish=True, ready=True)
        operation = AutomaticOperation(manager, "Main")
        backend = Mock()
        backend.write.side_effect = OSError("OPC write failed")

        with self.assertRaises(OSError):
            operation._publish(backend, "Dobot_1")
        manager.mark_finish_published.assert_not_called()

        backend.write.side_effect = None
        operation._publish(backend, "Dobot_1")
        manager.mark_finish_published.assert_called_once_with("Dobot_1")

    def test_connection_failure_exits_without_motion(self):
        manager = Mock()
        backend = Mock()
        backend.connect.side_effect = ConnectionError("offline")
        operation = AutomaticOperation(manager, "Main", lambda: backend)
        operation.start()
        operation.thread.join(timeout=3)
        self.assertFalse(operation.active)
        self.assertTrue(operation.stop_requested.is_set())
        manager.process.assert_not_called()
        backend.disconnect.assert_called_once()

    def test_bad_shutdown_log_explains_manual_reconnect(self):
        class BadShutdown(Exception):
            pass

        manager = Mock()
        backend = Mock()
        backend.connect.side_effect = BadShutdown(
            "The operation was cancelled because the application is shutting down."
        )
        operation = AutomaticOperation(manager, "Main", lambda: backend)

        operation.start()
        operation.thread.join(timeout=3)

        events = []
        while not operation.events.empty():
            events.append(operation.events.get_nowait())
        self.assertTrue(any(
            "OPC-UA 연결/재연결" in event and "KEPServerEX" in event
            for event in events
        ))

    def test_manual_mode_keeps_exchange_but_blocks_automatic_start(self):
        manager = Mock()
        operation = AutomaticOperation(
            manager, "Main", automatic_enabled=False
        )
        self.assertFalse(operation.automatic_enabled.is_set())

        operation.set_automatic(True, "Lamp_a")
        self.assertTrue(operation.automatic_enabled.is_set())
        self.assertEqual(operation.product, "Lamp_a")

        operation.set_automatic(False)
        self.assertFalse(operation.automatic_enabled.is_set())

    def test_last_dobot_2_cycle_reserves_start_for_next_product(self):
        """마지막 조립 중 START는 다음 제품의 완료 카운트 0을 기다린다."""
        d2_map = node_map_for_robot("Dobot_2")

        class Backend:
            def __init__(self):
                self.values = {
                    node_map_for_robot(name)["command.start"]: False
                    for name in ROBOT_NAMES
                }
                self.values[d2_map["input.target_count"]] = 2
                self.values[d2_map["counter.finish_count"]] = 1
                self.values[node_map_for_robot("Dobot_4")["input.product_type"]] = 2
                self.values[node_map_for_robot("Dobot_4")["command.base_start"]] = False
                self.reset_read = threading.Event()

            def connect(self):
                pass

            def disconnect(self):
                pass

            def read(self, nodes):
                if (nodes == [d2_map["input.target_count"], d2_map["counter.finish_count"]]
                        and self.values[d2_map["input.target_count"]] == 0
                        and self.values[d2_map["counter.finish_count"]] == 0):
                    self.reset_read.set()
                return {node: self.values[node] for node in nodes}

            def write(self, values):
                self.values.update(values)

        class Manager:
            def __init__(self):
                self.handshakes = {
                    name: RobotPlcHandshake(name) for name in ROBOT_NAMES
                }
                for handshake in self.handshakes.values():
                    handshake.mark_ready()
                self.calls = []
                self.first_started = threading.Event()
                self.release_first = threading.Event()
                self.second_started = threading.Event()

            def plc_response(self, name):
                return self.handshakes[name].response

            def mark_finish_published(self, name):
                self.handshakes[name].mark_finish_published()

            def mark_plc_disconnected(self, _reason):
                for handshake in self.handshakes.values():
                    handshake.mark_disconnected()

            def process(self, name, signals, *, automatic=False):
                self.assert_automatic = automatic
                self.calls.append((name, signals.completed_count))
                if len(self.calls) == 1:
                    self.first_started.set()
                    self.release_first.wait(timeout=5.0)
                else:
                    self.second_started.set()
                self.handshakes[name].mark_finished()

        backend = Backend()
        manager = Manager()
        operation = AutomaticOperation(manager, "Main", lambda: backend)
        operation.start()
        try:
            self.assertTrue(operation.connected.wait(timeout=2.0))
            self.assertTrue(self.wait_until(
                lambda: operation.latest_start["Dobot_2"] is False
            ))
            backend.values[d2_map["command.start"]] = True
            self.assertTrue(manager.first_started.wait(timeout=2.0))

            backend.values[d2_map["command.start"]] = False
            self.assertTrue(self.wait_until(
                lambda: operation.latest_start["Dobot_2"] is False
            ))
            backend.values[d2_map["command.start"]] = True
            self.assertTrue(self.wait_until(
                lambda: operation.latest_start["Dobot_2"] is True
            ))
            # 예약 슬롯이 찬 뒤 세 번째 START 상승은 새 작업으로 쌓지 않는다.
            backend.values[d2_map["command.start"]] = False
            self.assertTrue(self.wait_until(
                lambda: operation.latest_start["Dobot_2"] is False
            ))
            backend.values[d2_map["command.start"]] = True
            self.assertTrue(self.wait_until(
                lambda: operation.latest_start["Dobot_2"] is True
            ))
            manager.release_first.set()
            self.assertTrue(self.wait_until(
                lambda: manager.plc_response("Dobot_2").finish
            ))
            backend.values[d2_map["input.target_count"]] = 0
            backend.values[d2_map["counter.finish_count"]] = 0
            self.assertTrue(backend.reset_read.wait(timeout=2.0))
            self.assertTrue(self.wait_until(
                lambda: not manager.plc_response("Dobot_2").finish,
                timeout=3.0,
            ))
            self.assertFalse(manager.second_started.is_set())

            backend.values[d2_map["input.target_count"]] = 1
            backend.values[d2_map["counter.finish_count"]] = 0
            self.assertTrue(manager.second_started.wait(timeout=2.0))
            self.assertTrue(manager.assert_automatic)
            self.assertEqual(manager.calls, [("Dobot_2", 1), ("Dobot_2", 0)])
        finally:
            manager.release_first.set()
            operation.stop()
            operation.thread.join(timeout=3.0)
        self.assertFalse(operation.active)
        events = []
        while not operation.events.empty():
            events.append(operation.events.get_nowait())
        self.assertTrue(any("예약 1건 가득 참" in event for event in events))

    def test_dobot_4_base_start_runs_supply_and_writes_only_base_finish(self):
        d4_map = node_map_for_robot("Dobot_4")

        class Backend:
            def __init__(self):
                self.values = {
                    node_map_for_robot(name)["command.start"]: False
                    for name in ROBOT_NAMES
                }
                self.values[d4_map["command.base_start"]] = False
                self.values[d4_map["input.product_type"]] = 2

            def connect(self):
                pass

            def disconnect(self):
                pass

            def read(self, nodes):
                return {node: self.values[node] for node in nodes}

            def write(self, values):
                self.values.update(values)

        class Manager:
            def __init__(self):
                self.handshakes = {
                    name: RobotPlcHandshake(name) for name in ROBOT_NAMES
                }
                for handshake in self.handshakes.values():
                    handshake.mark_ready()
                self.started = threading.Event()
                self.base_finish = False

            def plc_response(self, name):
                return self.handshakes[name].response

            def process(self, _name, _signals, *, automatic=False):
                raise AssertionError("일반 제품 공정이 실행되면 안 됩니다.")

            def process_dobot_4_base_supply(self, *, automatic=False):
                self.automatic_speed_requested = automatic
                self.started.set()
                self.handshakes["Dobot_4"].mark_ready()
                self.base_finish = True

            def dobot_4_base_supply_finish(self):
                return self.base_finish

            def mark_dobot_4_base_supply_finish_published(self):
                pass

            def mark_finish_published(self, name):
                self.handshakes[name].mark_finish_published()

            def mark_plc_disconnected(self, _reason):
                self.base_finish = False
                for handshake in self.handshakes.values():
                    handshake.mark_disconnected()

        backend = Backend()
        manager = Manager()
        operation = AutomaticOperation(manager, "Main", lambda: backend)
        operation.start()
        try:
            self.assertTrue(operation.connected.wait(timeout=2.0))
            self.assertTrue(self.wait_until(
                lambda: operation.latest_dobot_4_base_start is False
            ))
            backend.values[d4_map["command.base_start"]] = True
            self.assertTrue(manager.started.wait(timeout=2.0))
            self.assertTrue(manager.automatic_speed_requested)
            self.assertTrue(self.wait_until(
                lambda: backend.values.get(d4_map["status.base_done"]) is True
            ))
            self.assertFalse(backend.values[d4_map["status.done"]])
        finally:
            operation.stop()
            operation.thread.join(timeout=3.0)

        self.assertFalse(operation.active)
