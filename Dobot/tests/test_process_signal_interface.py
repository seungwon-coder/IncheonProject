"""GUI와 OPC 입력이 같은 ProcessSignals로 변환되는지 장비 없이 검사한다."""

import unittest

from communication.mock_signal_source import MockSignalSource
from communication.process_signal_interface import GuiSignalProvider, OpcSignalProvider


class DictionaryOpcReader:
    """KEPServer 대신 테스트용 사전에서 논리 태그를 읽는 가짜 장치."""

    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def read_tags(self, robot_name: str) -> dict[str, object]:
        return dict(self.values)


class ProcessSignalInterfaceTest(unittest.TestCase):
    def test_gui_and_opc_create_the_same_dobot_1_signals(self) -> None:
        mock = MockSignalSource()
        mock.update("Dobot_1", start=True)
        gui_signals = GuiSignalProvider(mock).read("Dobot_1")

        opc = OpcSignalProvider(DictionaryOpcReader({
            "command.start": True,
        }))
        self.assertEqual(gui_signals, opc.read("Dobot_1"))

    def test_opc_dobot_2_work_count_is_preserved(self) -> None:
        opc = OpcSignalProvider(DictionaryOpcReader({
            "command.start": True,
            "input.work_count": 2,
        }))
        self.assertEqual(opc.read("Dobot_2").work_count, 2)

    def test_opc_dobot_4_product_code_becomes_process_name(self) -> None:
        opc = OpcSignalProvider(DictionaryOpcReader({
            "command.start": True,
            "input.product_type": 2,
        }))
        signals = opc.read("Dobot_4")
        self.assertEqual(signals.product_type, "Lamp_a")

    def test_invalid_opc_type_is_rejected_before_process_start(self) -> None:
        opc = OpcSignalProvider(DictionaryOpcReader({
            "command.start": 1,  # BOOL 자리에 정수가 오면 오기동을 막는다.
        }))
        with self.assertRaises(ValueError):
            opc.read("Dobot_1")

    def test_invalid_product_code_is_rejected(self) -> None:
        opc = OpcSignalProvider(DictionaryOpcReader({
            "command.start": True,
            "input.product_type": 99,
        }))
        with self.assertRaises(ValueError):
            opc.read("Dobot_4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
