"""Read the four START values without issuing robot commands."""
from communication.opcua_backend import OpcUaBackend
from communication.robot_opcua_tags import node_map_for_robot


def main():
    backend = OpcUaBackend()
    try:
        backend.connect()
        for number in range(1, 5):
            robot = f"Dobot_{number}"
            node_id = node_map_for_robot(robot)["command.start"]
            try:
                dv = backend.node(node_id).read_data_value(raise_on_bad_status=False)
                value = dv.Value.Value if dv.Value is not None else None
                print(f"{robot}: value={value!r}, quality={dv.StatusCode.name}, "
                      f"source_time={dv.SourceTimestamp}", flush=True)
            except Exception as exc:
                print(f"{robot}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        backend.disconnect()


if __name__ == "__main__":
    main()
