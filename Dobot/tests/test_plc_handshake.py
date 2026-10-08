"""실제 PLC 없이 START와 네 응답 신호의 순서를 검사한다."""

import unittest

from communication.plc_handshake import RobotPlcHandshake


class MemoryWriter:
    def __init__(self) -> None:
        self.values: dict[str, bool] = {}

    def write_values(self, values: dict[str, bool]) -> None:
        self.values.update(values)


class PlcHandshakeTest(unittest.TestCase):
    def test_start_requires_ready_and_rising_edge(self) -> None:
        handshake = RobotPlcHandshake("Dobot_1")
        self.assertFalse(handshake.set_start(True))
        handshake.set_start(False)
        handshake.mark_ready()
        self.assertTrue(handshake.set_start(True))
        self.assertFalse(handshake.set_start(True))

    def test_finish_pulses_once_after_successful_publish(self) -> None:
        now = [10.0]
        handshake = RobotPlcHandshake("Dobot_1", clock=lambda: now[0])
        handshake.mark_ready()
        self.assertTrue(handshake.set_start(True))
        handshake.mark_running()
        handshake.mark_finished()
        self.assertTrue(handshake.response.finish)
        self.assertTrue(handshake.response.ready)
        handshake.set_start(False)
        self.assertTrue(handshake.response.finish)
        now[0] += 5.0  # 전송 전에는 시간이 지나도 펄스를 버리지 않는다.
        self.assertTrue(handshake.response.finish)

        writer = MemoryWriter()
        handshake.publish(writer)
        self.assertTrue(writer.values["M1003"])
        now[0] += handshake.FINISH_PULSE_SECONDS - 0.01
        self.assertTrue(handshake.response.finish)
        self.assertFalse(handshake.set_start(True))
        now[0] += 0.02
        self.assertFalse(handshake.response.finish)
        self.assertTrue(handshake.response.ready)
        handshake.publish(writer)
        self.assertFalse(writer.values["M1003"])
        handshake.set_start(False)
        self.assertTrue(handshake.set_start(True))

    def test_failed_publish_does_not_consume_finish_pulse(self) -> None:
        now = [0.0]
        handshake = RobotPlcHandshake("Dobot_1", clock=lambda: now[0])
        handshake.mark_finished()
        writer = MemoryWriter()
        writer.write_values = lambda _values: (_ for _ in ()).throw(OSError("write failed"))

        with self.assertRaises(OSError):
            handshake.publish(writer)

        now[0] += 5.0
        self.assertTrue(handshake.response.finish)

    def test_error_is_not_cleared_only_by_start_off(self) -> None:
        handshake = RobotPlcHandshake("Dobot_3")
        handshake.mark_ready()
        handshake.set_start(True)
        handshake.mark_error()
        handshake.set_start(False)
        self.assertTrue(handshake.response.error)
        self.assertFalse(handshake.response.ready)
        handshake.mark_ready()  # GUI 초기화 또는 HOME 복귀 성공을 나타낸다.
        self.assertFalse(handshake.response.error)

    def test_logical_outputs_are_mapped_to_dobot_2_addresses(self) -> None:
        handshake = RobotPlcHandshake("Dobot_2")
        handshake.mark_ready()
        writer = MemoryWriter()
        handshake.publish(writer)
        self.assertEqual(writer.values, {
            "M1012": False,
            "M1013": False,
            "M1014": False,
            "M1015": True,
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
