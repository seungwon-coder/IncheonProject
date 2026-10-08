import unittest
from unittest.mock import patch

import numpy as np

from vision_core_server import Detection, assign_v3_object_rois, enrich_lamp_on, decide, predict_v3_seat_rois


def det(box, value="seat_unknown"):
    return Detection("car seat", value, .9, box, None)


class Vision3ObjectRoiTest(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((100, 200, 3), dtype=np.uint8)
        self.station = {
            "object_rois": {
                "seat1": {"polygon": [[.05,.1],[.48,.1],[.48,.9],[.05,.9]]},
                "seat2": {"polygon": [[.52,.1],[.95,.1],[.95,.9],[.52,.9]]},
                "led": {"polygon": [[.4,.3],[.6,.3],[.6,.7],[.4,.7]]},
            },
            "object_roi_min_overlap": .20,
            "led_on_brightness_threshold": 210,
            "led_brightness_percentile": 90,
        }

    def test_two_seats_are_assigned_to_two_distinct_rois(self):
        result = assign_v3_object_rois(self.station, self.frame, [det([15,20,85,80]), det([115,20,185,80])])
        self.assertEqual(2, len(result))

    def test_one_wide_detection_is_not_counted_twice(self):
        result = assign_v3_object_rois(self.station, self.frame, [det([30,20,170,80])])
        self.assertEqual(1, len(result))

    def test_adjacent_seat_seen_in_seat2_crop_is_rejected(self):
        """시트2 크롭의 여유영역에 시트1이 보여도 시트2로 인정하지 않습니다."""
        def fake_predict(_model, _station, _crop, _offset, region_key):
            # 두 크롭 모두 시트1 위치의 같은 객체를 봤다고 가정합니다.
            return [Detection(f"car seat [{region_key}]", "seat_unknown", .95, [15,20,85,80], None)]
        with patch("vision_core_server.predict_result_detections", side_effect=fake_predict):
            result=predict_v3_seat_rois(object(),self.station,self.frame)
        self.assertEqual(1,len(result))
        self.assertIn("[seat1]",result[0].name)

    def test_each_roi_accepts_only_its_own_seat(self):
        def fake_predict(_model, _station, _crop, _offset, region_key):
            box=[15,20,85,80] if region_key=="seat1" else [115,20,185,80]
            return [Detection(f"car seat [{region_key}]", "seat_unknown", .95, box, None)]
        with patch("vision_core_server.predict_result_detections", side_effect=fake_predict):
            result=predict_v3_seat_rois(object(),self.station,self.frame)
        self.assertEqual(2,len(result))
        self.assertEqual({"seat1","seat2"},{d.name.split("[")[-1].rstrip("]") for d in result})

    def test_led_fixed_roi_uses_current_brightness_even_when_baseline_is_bright(self):
        baseline = self.frame.copy(); current = self.frame.copy()
        baseline[30:70,80:120] = 255
        current[30:70,80:120] = 255
        result = enrich_lamp_on(self.station, baseline, current, [])
        self.assertEqual("lamp_on", result[-1].value)

    def test_led_fixed_roi_is_off_when_current_frame_is_dark(self):
        baseline = self.frame.copy(); current = self.frame.copy()
        baseline[30:70,80:120] = 255
        result = enrich_lamp_on(self.station, baseline, current, [])
        self.assertEqual("lamp_off", result[-1].value)

    def test_missing_led_roi_returns_configuration_error(self):
        station = {"id": 3, "inspection": "assembly", "object_rois": {}}
        result = enrich_lamp_on(station, None, self.frame, [])
        self.assertEqual("led_unconfigured", result[-1].value)
        self.assertEqual(("ERROR", 0), decide(station, result, 1, .6))

    def test_van_requires_both_seat_zones_and_truck_requires_seat1(self):
        lamp=Detection("LED ROI","lamp_on",1,[80,30,120,70],None)
        both=assign_v3_object_rois(self.station,self.frame,[det([15,20,85,80]),det([115,20,185,80])])+[lamp]
        only_seat2=assign_v3_object_rois(self.station,self.frame,[det([115,20,185,80])])+[lamp]
        self.station.update({"id":3,"inspection":"assembly"})
        self.assertEqual(("OK",0),decide(self.station,both,1,.6))
        self.assertEqual(("NG",0),decide(self.station,only_seat2,5,.6))


if __name__ == "__main__":
    unittest.main()
