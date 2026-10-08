"""KEPServerEX의 SCADA용 로봇 좌표 32개를 읽기 전용으로 확인한다.

이 검사는 로봇을 연결하거나 움직이지 않으며 OPC-UA 태그에도 값을 쓰지 않는다.
통합 GUI가 좌표를 전송 중이면 SCADA와 같은 값을 이 화면에서도 확인할 수 있다.
"""

from communication.opcua_backend import OpcUaBackend
from communication.robot_position_opc import POSITION_NODE_IDS
from robot_control.robot_position_cache import POSITION_AXES


def main() -> None:
    from asyncua import ua

    backend = OpcUaBackend()
    failures = 0
    try:
        backend.connect()
        for robot_name, mapping in POSITION_NODE_IDS.items():
            print(f"\n[{robot_name}]")
            for axis in POSITION_AXES:
                node_id = mapping[axis]
                try:
                    node = backend.node(node_id)
                    # raise_on_bad_status=False로 읽어 품질이 Bad여도 원인을 출력한다.
                    data = node.read_data_value(raise_on_bad_status=False)
                    data_type = node.read_data_type_as_variant_type()
                    value = data.Value.Value if data.Value is not None else None
                    quality = data.StatusCode
                    print(
                        f"{axis.upper():>2}  {data_type.name:<8} "
                        f"value={value!r:<12} quality={quality.name}"
                    )
                    if data_type is not ua.VariantType.Float or not quality.is_good():
                        failures += 1
                except Exception as exc:
                    failures += 1
                    print(f"{axis.upper():>2}  확인 실패: {type(exc).__name__}: {exc}")
    finally:
        backend.disconnect()
    if failures:
        raise SystemExit(f"\n좌표 태그 점검 실패: {failures}/32개")
    print("\n좌표 태그 32개: Float / Good 확인 완료")


if __name__ == "__main__":
    main()
