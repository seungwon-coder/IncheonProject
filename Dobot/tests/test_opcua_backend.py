"""Integration checks against a local OPC UA server; no PLC writes."""

import socket
import unittest
from queue import Queue

from asyncua import ua
from asyncua.sync import Server

from communication.opcua_backend import OpcUaBackend


class OpcUaBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.endpoint = f"opc.tcp://127.0.0.1:{port}"
        cls.server = Server()
        cls.server.set_endpoint(cls.endpoint)
        idx = cls.server.register_namespace("urn:dobot:test")
        cls.tag = cls.server.nodes.objects.add_variable(
            idx, "TestCount", ua.Variant(3, ua.VariantType.Int16))
        cls.tag.set_writable()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.backend = OpcUaBackend(self.endpoint, node_map={
            "D1011": self.tag.nodeid.to_string()})
        self.backend.connect()

    def tearDown(self):
        self.backend.disconnect()

    def test_mapped_read_and_typed_write(self):
        self.backend.write({"D1011": 42})
        self.assertEqual(self.backend.read(["D1011"]), {"D1011": 42})
        self.assertEqual(self.tag.read_data_value().Value.VariantType, ua.VariantType.Int16)

    def test_bad_node_and_unmapped_address_raise(self):
        with self.assertRaises(ua.UaStatusCodeError):
            self.backend.read(["ns=2;s=missing"])
        with self.assertRaises(ValueError):
            self.backend.read(["M1001"])

    def test_subscription_delivers_value(self):
        changes = Queue()

        class Handler:
            def datachange_notification(self, node, val, data):
                changes.put(val)

        sub = self.backend.client.create_subscription(50, Handler())
        try:
            sub.subscribe_data_change(self.backend.node("D1011"))
            changes.get(timeout=5)
            self.backend.write({"D1011": 87})
            self.assertEqual(changes.get(timeout=5), 87)
        finally:
            sub.delete()


if __name__ == "__main__":
    unittest.main()
