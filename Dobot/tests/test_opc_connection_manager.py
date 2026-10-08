"""실제 KEPServer 없이 OPC 단절·재연결과 주소 변환을 검사한다."""

import unittest

from communication.opc_connection_manager import (
    OpcConnectionManager,
    OpcConnectionState,
    OpcUnavailableError,
    PlcAddressOpcAdapter,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeBackend:
    def __init__(self, *, connect_error=False, read_error=False) -> None:
        self.connect_error = connect_error
        self.read_error = read_error
        self.values: dict[str, object] = {}
        self.closed = False

    def connect(self) -> None:
        if self.connect_error:
            raise ConnectionError("가짜 서버 연결 실패")

    def disconnect(self) -> None:
        self.closed = True

    def read(self, item_ids):
        if self.read_error:
            raise ConnectionError("가짜 읽기 단절")
        return {item: self.values[item] for item in item_ids}

    def write(self, values) -> None:
        self.values.update(values)


class OpcConnectionManagerTest(unittest.TestCase):
    def test_failed_connect_waits_then_reconnects(self) -> None:
        clock = FakeClock()
        backends = [FakeBackend(connect_error=True), FakeBackend()]
        manager = OpcConnectionManager(
            lambda: backends.pop(0), retry_seconds=2.0, clock=clock
        )
        self.assertFalse(manager.connect_if_due())
        self.assertIs(manager.state, OpcConnectionState.RETRY_WAIT)
        clock.now = 1.9
        self.assertFalse(manager.connect_if_due())
        clock.now = 2.0
        self.assertTrue(manager.connect_if_due())
        self.assertIs(manager.state, OpcConnectionState.CONNECTED)

    def test_read_failure_enters_retry_and_reports_loss(self) -> None:
        lost: list[str] = []
        backend = FakeBackend(read_error=True)
        manager = OpcConnectionManager(lambda: backend, on_connection_lost=lost.append)
        self.assertTrue(manager.connect_if_due())
        with self.assertRaises(OpcUnavailableError):
            manager.read(["M1001"])
        self.assertIs(manager.state, OpcConnectionState.RETRY_WAIT)
        self.assertTrue(backend.closed)
        self.assertEqual(len(lost), 1)

    def test_adapter_maps_dobot_2_input_addresses(self) -> None:
        backend = FakeBackend()
        backend.values = {"M1011": True, "D1011": 2}
        manager = OpcConnectionManager(lambda: backend)
        manager.connect_if_due()
        values = PlcAddressOpcAdapter(manager).read_robot_inputs("Dobot_2")
        self.assertEqual(values, {"command.start": True, "input.work_count": 2})

    def test_adapter_maps_logical_outputs_to_addresses(self) -> None:
        backend = FakeBackend()
        manager = OpcConnectionManager(lambda: backend)
        manager.connect_if_due()
        PlcAddressOpcAdapter(manager).write_robot_outputs("Dobot_1", {
            "status.busy": False,
            "status.done": True,
            "status.error": False,
            "status.ready": True,
        })
        self.assertEqual(backend.values, {
            "M1002": False,
            "M1003": True,
            "M1004": False,
            "M1005": True,
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
