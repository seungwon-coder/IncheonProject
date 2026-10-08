"""Read-only KEPServerEX connection, browse, read and subscription CLI."""

import argparse
import os
import sys
import time

from communication.opcua_backend import DEFAULT_ENDPOINT, OpcUaBackend


class PrintChanges:
    def datachange_notification(self, node, value, data):
        dv = data.monitored_item.Value
        print(f"{node.nodeid.to_string()} value={value!r} "
              f"quality={dv.StatusCode} source_time={dv.SourceTimestamp}", flush=True)

    def status_change_notification(self, status):
        print(f"Subscription status: {status}", flush=True)


def browse(node, depth, seen=None):
    seen = set() if seen is None else seen
    key = node.nodeid.to_string()
    if key in seen:
        return
    seen.add(key)
    print(f"{key}  {node.read_display_name().Text}")
    if depth > 0:
        for child in node.get_children():
            browse(child, depth - 1, seen)


def print_group(group, client):
    """Follow object folders only; do not report tag metadata as process tags."""
    from asyncua import ua
    pending = [(group, "")]
    seen = set()
    while pending:
        parent, path = pending.pop()
        key = parent.nodeid.to_string()
        if key in seen:
            continue
        seen.add(key)
        for node in parent.get_children():
            name = node.read_display_name().Text or node.nodeid.to_string()
            label = f"{path}/{name}" if path else name
            kind = node.read_node_class()
            if kind == ua.NodeClass.Object:
                pending.append((node, label))
            elif kind == ua.NodeClass.Variable:
                dtype = "?"
                try:
                    dtype = client.get_node(node.read_data_type()).read_display_name().Text
                    dv = node.read_data_value(raise_on_bad_status=False)
                    value = dv.Value.Value if dv.Value is not None else None
                    quality = f"{dv.StatusCode.name} (0x{dv.StatusCode.value:08X})"
                    print(f"{label}\t{dtype}\t{value!r}\t{quality}", flush=True)
                except ua.UaStatusCodeError as exc:
                    print(f"{label}\t{dtype}\t<unavailable>\t{exc}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--username", default=os.getenv("OPCUA_USERNAME"))
    parser.add_argument("--security", default=os.getenv("OPCUA_SECURITY"),
                        help="Policy,Mode,certificate,private_key[,server_certificate]")
    parser.add_argument("--application-uri", default="urn:dobot:opcua:client")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    groups = commands.add_parser("groups", help="지정 그룹 아래 태그 정보 조회")
    groups.add_argument("nodes", nargs="*", default=[
        "ns=2;s=M.PLC.ROBOT_I/O", "ns=2;s=M.PLC.VISION_I/O"])
    tree = commands.add_parser("browse")
    tree.add_argument("--node", default="i=85")
    tree.add_argument("--depth", type=int, choices=range(0, 11), default=3)
    for name in ("read", "watch", "info"):
        cmd = commands.add_parser(name)
        cmd.add_argument("nodes", nargs="+")
    args = parser.parse_args()
    backend = OpcUaBackend(args.endpoint, username=args.username,
                           password=os.getenv("OPCUA_PASSWORD"), security=args.security,
                           application_uri=args.application_uri)
    try:
        backend.connect()
        if args.command == "check":
            print(f"Connected: {args.endpoint}")
            for index, uri in enumerate(backend.client.get_namespace_array()):
                print(f"ns={index}: {uri}")
        elif args.command == "browse":
            browse(backend.node(args.node), args.depth)
        elif args.command == "groups":
            for item in args.nodes:
                print(f"\n[{item}]", flush=True)
                print("Tag\tDataType\tValue\tQuality", flush=True)
                print_group(backend.node(item), backend.client)
        elif args.command == "info":
            for item in args.nodes:
                node = backend.node(item)
                dtype = node.read_data_type()
                print(f"NodeId: {item}")
                print(f"DataType: {backend.client.get_node(dtype).read_display_name().Text} ({dtype.to_string()})")
                print(f"ValueRank: {node.read_value_rank()}")
                dv = node.read_data_value(raise_on_bad_status=False)
                print(f"Value: {dv.Value.Value if dv.Value is not None else None!r}")
                print(f"Quality: {dv.StatusCode}")
                print(f"SourceTimestamp: {dv.SourceTimestamp}")
                for prop in node.get_properties():
                    print(f"Property: {prop.read_browse_name().Name} ({prop.nodeid.to_string()})")
        elif args.command == "read":
            for item, value in backend.read(args.nodes).items():
                print(f"{item} = {value!r}")
        else:
            sub = backend.client.create_subscription(250, PrintChanges())
            try:
                for item in args.nodes:
                    sub.subscribe_data_change(backend.node(item))
                print("구독 중입니다. 종료: Ctrl+C", flush=True)
                while True:
                    time.sleep(1)
                    # Detect a broken connection even when no tag changes arrive.
                    backend.node("i=2258").read_value()
            finally:
                sub.delete()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"OPC UA 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            backend.disconnect()
        except Exception as exc:
            print(f"연결 종료 오류: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
