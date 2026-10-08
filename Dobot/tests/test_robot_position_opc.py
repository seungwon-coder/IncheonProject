"""실제 Dobot·KEPServer 없이 SCADA 좌표 전달 구조를 검사한다."""

import multiprocessing as mp
import threading
import unittest

from communication.robot_position_opc import (
    POSITION_NODE_IDS,
    RobotPositionOpcPublisher,
    build_position_values,
)
from robot_control.dobot_config import ROBOT_NAMES
from robot_control.robot_position_cache import (
    POSITION_AXES,
    PositionCaptureSdk,
    publish_position,
    read_position,
)


class FakeWorker:
    def __init__(self, sample):
        self.sample = sample

    def latest_position(self, _max_age_seconds):
        return self.sample


class FakeBackend:
    def __init__(self):
        self.connected = False
        self.values = {}
        self.written = threading.Event()

    def connect(self):
        self.connected = True

    def write(self, values):
        self.values.update(values)
        self.written.set()

    def disconnect(self):
        self.connected = False


class FakeSdk:
    marker = "SDK 속성 전달 확인"

    def GetPose(self, _api):
        return [1, 2, 3, 4, 5, 6, 7, 8]


class RobotPositionOpcTests(unittest.TestCase):
    def test_all_32_exact_node_ids_are_defined(self):
        self.assertEqual(sum(map(len, POSITION_NODE_IDS.values())), 32)
        for number in range(1, 5):
            for axis in POSITION_AXES:
                self.assertEqual(
                    POSITION_NODE_IDS[f"Dobot_{number}"][axis],
                    f"ns=2;s=M.PLC.ROBOT_I/O.Coord.ROBOT{number}_{axis.upper()}",
                )

    def test_sdk_pose_is_copied_without_changing_result(self):
        cache = mp.get_context("spawn").Array("d", 9, lock=True)
        sdk = PositionCaptureSdk(FakeSdk(), cache)
        raw = sdk.GetPose(object())
        self.assertEqual(raw, [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(read_position(cache), dict(zip(POSITION_AXES, map(float, raw))))
        self.assertEqual(sdk.marker, "SDK 속성 전달 확인")

    def test_invalid_or_incomplete_pose_is_not_published(self):
        cache = mp.get_context("spawn").Array("d", 9, lock=True)
        publish_position(cache, [1, 2, 3])
        self.assertIsNone(read_position(cache))
        publish_position(cache, [1, 2, 3, 4, 5, 6, 7, float("nan")])
        self.assertIsNone(read_position(cache))

    def test_values_are_float_and_missing_robot_is_isolated(self):
        sample = {axis: index for index, axis in enumerate(POSITION_AXES)}
        workers = {name: FakeWorker(sample) for name in ROBOT_NAMES}
        workers["Dobot_3"] = FakeWorker(None)
        values, missing = build_position_values(workers)
        self.assertEqual(len(values), 24)
        self.assertEqual(missing, ["Dobot_3"])
        self.assertTrue(all(type(value) is float for value in values.values()))

    def test_publisher_connects_writes_and_stops(self):
        sample = {axis: float(index) for index, axis in enumerate(POSITION_AXES)}
        workers = {name: FakeWorker(sample) for name in ROBOT_NAMES}
        backend = FakeBackend()
        publisher = RobotPositionOpcPublisher(
            workers, lambda: backend, interval_seconds=0.5, retry_seconds=1.0
        )
        publisher.start()
        self.assertTrue(backend.written.wait(2.0))
        publisher.stop()
        self.assertFalse(publisher.active)
        self.assertEqual(len(backend.values), 32)
        self.assertFalse(backend.connected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
