"""V13.28 비전3 색상 유무 결과와 기존 LED 로직 결합 규칙 시험."""
import unittest

from vision_core_server import Detection, decide


def item(key, present):
    return Detection(f"색상비율 30.0% [{key}]",
                     "seat_unknown" if present else "seat_absent", 1.0, [0, 0, 10, 10], None)


def led(on):
    return Detection("LED ROI", "lamp_on" if on else "lamp_off", 1.0, [0, 0, 2, 2], None)


class Vision3ColorPresenceRuleTest(unittest.TestCase):
    def setUp(self):
        self.station={"id":3,"inspection":"assembly","object_rois":{"seat1":{},"seat2":{},"led":{}}}

    def test_van_two_seats_and_led_on_is_ok(self):
        self.assertEqual(decide(self.station,[item("seat1",True),item("seat2",True),led(True)],1,.6),("OK",0))

    def test_missing_second_seat_is_ng(self):
        self.assertEqual(decide(self.station,[item("seat1",True),item("seat2",False),led(True)],1,.6),("NG",0))

    def test_led_off_is_ng_and_existing_logic_is_preserved(self):
        self.assertEqual(decide(self.station,[item("seat1",True),item("seat2",True),led(False)],1,.6),("NG",0))


if __name__ == "__main__":
    unittest.main()
