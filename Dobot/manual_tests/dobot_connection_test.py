"""Dobot Magician USB 통신 점검 프로그램.

이 파일은 로봇을 움직이지 않고 다음 작업만 수행한다.
1. PC에 연결된 Dobot 후보 COM 포트를 검색한다.
2. 각 포트에 연결하여 실제 Dobot인지 확인한다.
3. 결과를 화면과 JSON 로그 파일에 기록한다.
4. 확인이 끝난 장치의 연결을 즉시 해제한다.

안전을 위해 HOME, JOG, PTP, 그리퍼, 흡착컵 명령은 사용하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import queue
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


# __file__은 현재 실행 중인 이 파일의 경로이다. 이 값을 기준으로 경로를 만들면
# 프로그램을 어느 폴더에서 실행하더라도 MAIN 내부의 SDK를 정확하게 찾을 수 있다.
# 파일이 manual_tests로 이동해도 SDK와 로그는 기존 MAIN 폴더를 기준으로 찾는다.
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SDK_DIR = (BASE_DIR / "sdk").resolve()
DEFAULT_LOG_DIR = BASE_DIR / "logs"


@dataclass
class DeviceResult:
    """포트 한 개의 검사 결과를 묶어서 보관하는 자료형."""
    index: int
    port: str
    connected: bool
    status_code: int | None
    status: str
    device_type: str = ""
    firmware_name: str = ""
    firmware_version: str = ""
    runtime_seconds: float | None = None
    error: str = ""


CONNECT_STATUS = {
    0: "정상",
    1: "장치를 찾을 수 없음",
    2: "다른 프로그램에서 사용 중",
}

DEVICE_TYPE = {
    0: "Idle",
    1: "Controller",
    2: "Magician",
    3: "Magician Lite",
}


def load_sdk(sdk_dir: Path) -> Any:
    """지정한 폴더의 제조사 SDK를 불러온다. 기본 경로는 MAIN 내부 sdk이다."""
    wrapper = sdk_dir / "DobotDllType.py"
    dll = sdk_dir / "DobotDll.dll"
    if not wrapper.is_file():
        raise FileNotFoundError(f"SDK 래퍼 파일이 없습니다: {wrapper}")
    if not dll.is_file():
        raise FileNotFoundError(f"SDK DLL 파일이 없습니다: {dll}")

    # Python이 DobotDllType.py를 import할 수 있도록 검색 경로 맨 앞에 추가한다.
    sys.path.insert(0, str(sdk_dir))
    import DobotDllType as dType  # type: ignore

    return dType


def normalize_ports(found: list[Any]) -> list[str]:
    """SDK 검색 결과에서 빈 값과 중복 포트를 제거한다."""
    ports: list[str] = []
    for item in found:
        port = str(item).strip()
        if port and port not in ports:
            ports.append(port)
    return ports


def test_port(api: Any, d_type: Any, port: str, index: int) -> DeviceResult:
    """포트 하나에 연결해 Dobot 응답 여부만 확인하고 반드시 연결을 해제한다."""
    api_opened = False
    try:
        response = d_type.ConnectDobot(api, port, 115200)
        code = int(response[0])
        api_opened = code == d_type.DobotConnect.DobotConnect_NoError
        if not api_opened:
            return DeviceResult(
                index=index,
                port=port,
                connected=False,
                status_code=code,
                status=CONNECT_STATUS.get(code, f"알 수 없는 상태 코드 {code}"),
            )

        # ConnectDobot은 컨트롤러와 실제 데이터를 주고받고 펌웨어 정보도 반환한다.
        # 따라서 별도의 이동 명령 없이도 통신 성공 여부를 판단할 수 있다.
        master_type = int(response[1])
        slave_type = int(response[2])
        effective_type = master_type if master_type != 1 else slave_type
        is_robot = effective_type in (
            d_type.DevType.Magician,
            d_type.DevType.MagicianLite,
        )
        return DeviceResult(
            index=index,
            port=port,
            connected=is_robot,
            status_code=code,
            status=CONNECT_STATUS[0] if is_robot else "Dobot 로봇으로 식별되지 않음",
            device_type=DEVICE_TYPE.get(effective_type, str(effective_type)),
            firmware_name=str(response[3]),
            firmware_version=str(response[4]),
            runtime_seconds=float(response[7]),
        )
    except Exception as exc:
        return DeviceResult(
            index=index,
            port=port,
            connected=False,
            status_code=None,
            status="검사 오류",
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        # 오류가 발생해도 포트를 계속 잡고 있지 않도록 finally에서 해제한다.
        if api_opened:
            try:
                d_type.DisconnectDobot(api)
            except Exception:
                pass


def _test_port_process(
    sdk_dir: str,
    port: str,
    index: int,
    result_queue: mp.Queue,
) -> None:
    """포트 하나를 검사하는 자식 프로세스의 시작 함수.

    제조사 SDK 내부에서 통신이 멈추더라도 메인 프로그램까지 멈추지 않도록
    포트 검사는 별도 프로세스에서 실행한다.
    """
    try:
        d_type = load_sdk(Path(sdk_dir))
        api = d_type.load()
        result_queue.put(asdict(test_port(api, d_type, port, index)))
    except Exception as exc:
        result_queue.put(
            asdict(
                DeviceResult(
                    index=index,
                    port=port,
                    connected=False,
                    status_code=None,
                    status="검사 프로세스 오류",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        )


def test_port_with_timeout(
    sdk_dir: Path,
    port: str,
    index: int,
    timeout: float,
) -> DeviceResult:
    """포트를 제한 시간 동안 검사하고 무응답 프로세스만 종료한다."""
    context = mp.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_test_port_process,
        name=f"dobot-port-test-{port}",
        args=(str(sdk_dir), port, index, result_queue),
    )
    process.start()
    try:
        # Queue.get의 timeout이 포트 한 개가 기다릴 수 있는 최대 시간이다.
        data = result_queue.get(timeout=timeout)
        return DeviceResult(**data)
    except queue.Empty:
        return DeviceResult(
            index=index,
            port=port,
            connected=False,
            status_code=None,
            status=f"응답 시간 초과 ({timeout:g}초)",
        )
    finally:
        # 정상 결과를 받았으면 잠깐 기다리고, SDK가 끝나지 않으면 강제 종료한다.
        process.join(1.0)
        if process.is_alive():
            process.terminate()
            process.join(2.0)
        result_queue.close()


def save_log(results: list[DeviceResult], expected: int, log_dir: Path) -> Path:
    """나중에 통신 이력을 확인할 수 있도록 검사 결과를 JSON으로 저장한다."""
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone()
    path = log_dir / f"dobot_connection_{timestamp:%Y%m%d_%H%M%S}.json"
    payload = {
        "checked_at": timestamp.isoformat(timespec="seconds"),
        "expected_devices": expected,
        "detected_ports": len(results),
        "connected_devices": sum(result.connected for result in results),
        "results": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def print_report(results: list[DeviceResult], expected: int) -> None:
    """검사 결과를 사람이 한눈에 보기 쉬운 표 형태로 출력한다."""
    connected = sum(result.connected for result in results)
    print("\nDobot USB 통신 점검 결과")
    print("-" * 78)
    if not results:
        print("검색된 Dobot 포트가 없습니다.")
    for result in results:
        label = f"Dobot 후보 {result.index}"
        if result.connected:
            detail = " / ".join(
                value
                for value in (
                    result.device_type,
                    result.firmware_name,
                    result.firmware_version,
                )
                if value
            )
            print(f"{label:<14} {result.port:<18} 연결 성공  {detail}")
        else:
            suffix = f" ({result.error})" if result.error else ""
            print(f"{label:<14} {result.port:<18} 연결 실패  {result.status}{suffix}")
    print("-" * 78)
    print(f"결과: {connected}/{expected}대 통신 정상")


def parse_args() -> argparse.Namespace:
    """명령 프롬프트에서 입력한 실행 옵션을 해석한다."""
    parser = argparse.ArgumentParser(
        description="Dobot을 움직이지 않고 USB 통신 여부만 점검합니다."
    )
    parser.add_argument("--expected", type=int, default=4, help="예상 Dobot 수 (기본값: 4)")
    parser.add_argument(
        "--sdk-dir", type=Path, default=DEFAULT_SDK_DIR, help="Dobot SDK 폴더"
    )
    parser.add_argument(
        "--ports",
        nargs="*",
        help="자동 검색 대신 검사할 포트 지정 (예: --ports COM3 COM4)",
    )
    parser.add_argument(
        "--port-timeout",
        type=float,
        default=8.0,
        help="포트 한 개의 최대 응답 대기 시간(초, 기본값: 8)",
    )
    parser.add_argument("--no-log", action="store_true", help="JSON 결과 로그를 저장하지 않음")
    return parser.parse_args()


def main() -> int:
    """포트 검색부터 결과 저장까지 전체 검사 순서를 실행한다."""
    # Windows 터미널에서도 한글이 깨지지 않도록 출력 인코딩을 맞춘다.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if args.expected < 1:
        print("오류: --expected 값은 1 이상이어야 합니다.", file=sys.stderr)
        return 2
    if args.port_timeout <= 0:
        print("오류: --port-timeout 값은 0보다 커야 합니다.", file=sys.stderr)
        return 2

    try:
        d_type = load_sdk(args.sdk_dir.resolve())
        api = d_type.load()
        # --ports가 있으면 지정 포트만 검사하고, 없으면 SDK로 자동 검색한다.
        discovered = args.ports if args.ports is not None else d_type.SearchDobot(api)
        ports = normalize_ports(discovered)
        print(f"검색된 포트: {', '.join(ports) if ports else '없음'}")
        # 실제 포트 검사는 각각 독립 프로세스에서 실행한다. 한 포트가 멈춰도
        # 제한 시간이 지나면 다음 포트 검사를 계속할 수 있다.
        results = [
            test_port_with_timeout(args.sdk_dir.resolve(), port, index, args.port_timeout)
            for index, port in enumerate(ports, start=1)
        ]
    except Exception as exc:
        print(f"통신 점검을 시작할 수 없습니다: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print_report(results, args.expected)
    if not args.no_log:
        try:
            log_path = save_log(results, args.expected, DEFAULT_LOG_DIR)
            print(f"로그: {log_path}")
        except OSError as exc:
            print(f"경고: 로그 저장 실패: {exc}", file=sys.stderr)

    # 종료 코드 0은 정상, 1은 예상 대수 불일치, 2는 실행 오류를 뜻한다.
    return 0 if sum(result.connected for result in results) == args.expected else 1


if __name__ == "__main__":
    # Windows에서 자식 프로세스를 안전하게 시작하기 위한 필수 처리다.
    mp.freeze_support()
    raise SystemExit(main())
