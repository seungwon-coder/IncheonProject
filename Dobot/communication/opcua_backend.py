"""KEPServerEX OPC UA backend compatible with OpcConnectionManager."""

from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_ENDPOINT = "opc.tcp://127.0.0.1:49320"


class OpcUaBackend:
    """Use exact NodeIds, or explicitly map PLC addresses to NodeIds.

    Calls are blocking: use from a worker, not the GUI thread. Writes are
    sequential and are not atomic; a failed write must not be blindly replayed.
    """

    def __init__(self, endpoint=DEFAULT_ENDPOINT, *, node_map=None,
                 username=None, password=None, security=None, timeout=5.0,
                 application_uri="urn:dobot:opcua:client"):
        self.endpoint = endpoint
        self.node_map = dict(node_map or {})
        self.username = username
        self.password = password
        self.security = security
        self.timeout = timeout
        self.application_uri = application_uri
        self.client = None

    def connect(self):
        if self.client is not None:
            return
        from asyncua.sync import Client
        client = Client(self.endpoint, timeout=self.timeout)
        try:
            client.application_uri = self.application_uri
            if self.security:
                client.set_security_string(self.security)
            if self.username:
                client.set_user(self.username)
            if self.password is not None:
                client.set_password(self.password)
            client.connect()
        except BaseException:
            try:
                client.disconnect()
            except Exception:
                pass
            raise
        self.client = client

    def disconnect(self):
        client, self.client = self.client, None
        if client is not None:
            client.disconnect()

    def node(self, item_id):
        if self.client is None:
            raise ConnectionError("OPC UA 서버에 연결되지 않았습니다.")
        node_id = self.node_map.get(item_id, item_id)
        if not node_id.startswith(("ns=", "i=", "s=", "g=", "b=")):
            raise ValueError(f"정확한 NodeId 또는 node_map 매핑이 필요합니다: {item_id}")
        return self.client.get_node(node_id)

    def read(self, item_ids: Sequence[str]) -> dict[str, Any]:
        values = {}
        for item in item_ids:
            dv = self.node(item).read_data_value()
            if not dv.StatusCode.is_good():
                raise ValueError(f"{item}: OPC 품질이 Good이 아닙니다: {dv.StatusCode}")
            values[item] = dv.Value.Value
        return values

    def write(self, values: Mapping[str, Any]) -> None:
        from asyncua import ua
        for item, value in values.items():
            node = self.node(item)
            variant_type = node.read_data_type_as_variant_type()
            # Value only: do not send server/source timestamps to KEPServerEX.
            node.write_value(ua.DataValue(ua.Variant(value, variant_type)))
