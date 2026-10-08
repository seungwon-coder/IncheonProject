"""수동·자동 모드 공용 OPC-UA 신호 교환기.

OPC-UA 호출은 이 모듈의 한 스레드만 담당한다. 로봇별 실제 공정은 독립 Worker와
스레드 풀에서 실행하므로 한 로봇이 움직이는 동안에도 다른 로봇의 START와 상태를
계속 확인할 수 있다.
"""
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from robot_control.dobot_config import ROBOT_NAMES
from communication.opcua_backend import OpcUaBackend
from communication.opc_tag_contract import PRODUCT_TYPE_CODES
from robot_control.process_state_machine import ProcessSignals
from communication.robot_opcua_tags import node_map_for_robot
from communication.robot_position_opc import build_position_values


class StartEdges:
    """PLC START가 OFF에서 ON으로 바뀌는 순간만 한 번 검출한다."""

    def __init__(self) -> None:
        self.previous = {
            **{name: True for name in ROBOT_NAMES},
            "Dobot_4.base_supply": True,
        }

    def consume(self, name: str, value: bool) -> bool:
        """현재 값을 저장하고 새로운 상승 에지인지 반환한다."""
        if type(value) is not bool:
            raise ValueError(f"{name}: START must be Boolean")
        rising = value and not self.previous[name]
        self.previous[name] = value
        return rising


@dataclass(frozen=True)
class PendingStart:
    """실행 중 받은 다음 START 한 건의 제품 정보 또는 PLC 카운터 조건."""

    signals: ProcessSignals | None = None
    next_dobot_2_product: bool = False
    dobot_4_base_supply: bool = False


def cycle_signals(
    name: str, values: dict[str, object], product: str | None = None
) -> ProcessSignals:
    """PLC 태그를 공정 상태 머신이 사용하는 안전한 입력 형식으로 바꾼다."""
    if name == "Dobot_4":
        # START가 들어온 바로 그 순간에 읽은 분류값 하나로 이번 사이클 경로를
        # 고정한다. 운전 중 비전 태그가 바뀌어도 목적지는 바뀌지 않는다.
        product_code = values.get("input.product_type")
        if type(product_code) is not int:
            raise ValueError("Dobot_4 비전 분류값은 정수여야 합니다.")
        try:
            product_type = PRODUCT_TYPE_CODES[product_code]
        except KeyError as exc:
            allowed = ", ".join(str(code) for code in PRODUCT_TYPE_CODES)
            raise ValueError(
                f"Dobot_4 비전 분류값은 {allowed} 중 하나여야 합니다."
            ) from exc
        return ProcessSignals(start=True, product_type=product_type)
    if name != "Dobot_2":
        return ProcessSignals(start=True, product_type=product)
    target, finished = (values[key] for key in (
        "input.target_count", "counter.finish_count"))
    if any(type(v) is not int for v in (target, finished)):
        raise ValueError(
            f"Dobot_2 목표/완료 횟수는 정수여야 합니다: 목표={target!r}, 완료={finished!r}"
        )
    if target not in (1, 2) or not 0 <= finished < target:
        raise ValueError(
            f"Dobot_2 목표/완료 횟수가 기동 조건에 맞지 않습니다: "
            f"목표={target}, 완료={finished}"
        )
    return ProcessSignals(start=True, work_count=target, completed_count=finished)


class AutomaticOperation:
    """수동·자동 모드에서 하나의 OPC-UA 연결을 계속 유지한다.

    수동 모드에서는 START를 읽어 현재 값을 표시하고 출력 상태만 기록한다.
    자동 모드에서는 로봇별 실행 중 새 START 상승 에지를 최대 한 건 예약한다.
    Dobot_2의 첫 번째 조립 중 START는 예약하지 않고 마지막 조립 중 START만
    다음 제품 요청으로 예약한다.
    """

    def __init__(
        self, manager, product, backend_factory=OpcUaBackend, *, automatic_enabled=True
    ):
        self.manager = manager
        self.product = product
        self.backend_factory = backend_factory
        self.events = queue.Queue()
        self.stop_requested = threading.Event()
        self.connected = threading.Event()
        self.automatic_enabled = threading.Event()
        if automatic_enabled:
            self.automatic_enabled.set()
        # GUI가 PLC에서 받은 START를 로봇별로 표시할 때 읽는다.
        # 연결 전에는 실제 값이 아직 없다는 뜻으로 None을 사용한다.
        self.latest_start = {name: None for name in ROBOT_NAMES}
        self.latest_dobot_4_base_start = None
        # START·상태·SCADA 좌표가 같은 OPC-UA 세션을 사용한다. 별도 좌표 세션을
        # 만들지 않아 KEPServerEX의 동시 세션 제한과 연결 부하를 피한다.
        self.workers = getattr(getattr(manager, "fleet", None), "workers", None)
        self._next_position_publish = 0.0
        self._last_position_status = ""
        self.admission_lock = threading.Lock()
        self.thread = None

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        """백그라운드 OPC-UA 신호 교환을 시작한다."""
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """새 자동 기동을 막고 진행 중 공정이 끝나면 통신을 종료한다."""
        # 종료 중에는 새 사이클을 받지 않고 진행 중 사이클만 정상 종료한다.
        with self.admission_lock:
            self.stop_requested.set()

    def set_automatic(self, enabled: bool, product: str | None = None) -> None:
        """OPC 연결은 유지한 채 PLC START의 실제 기동 허용 여부만 바꾼다."""
        if product is not None:
            self.product = product
        with self.admission_lock:
            if enabled:
                self.automatic_enabled.set()
            else:
                self.automatic_enabled.clear()
        mode = "자동" if enabled else "수동"
        self.events.put(f"OPC-UA 통신 유지 / {mode} 모드")

    def _publish(self, backend: OpcUaBackend, name: str) -> None:
        """한 로봇의 네 출력 상태를 확정된 KEPServerEX 태그에 기록한다."""
        response = self.manager.plc_response(name)
        mapping = node_map_for_robot(name)
        values = {mapping[key]: value for key, value in (
            ("status.busy", response.running), ("status.done", response.finish),
            ("status.ready", response.ready and not self.stop_requested.is_set()),
            ("status.error", response.error))}
        base_finish = False
        if name == "Dobot_4":
            getter = getattr(self.manager, "dobot_4_base_supply_finish", None)
            base_finish = getter() is True if callable(getter) else False
            values[mapping["status.base_done"]] = base_finish
        backend.write(values)
        if response.finish:
            self.manager.mark_finish_published(name)
        if base_finish:
            marker = getattr(
                self.manager, "mark_dobot_4_base_supply_finish_published", None
            )
            if callable(marker):
                marker()

    def _publish_positions(self, backend: OpcUaBackend) -> None:
        """같은 OPC-UA 연결로 네 로봇의 최신 좌표 32개를 약 1초마다 기록한다."""
        if self.workers is None or time.monotonic() < self._next_position_publish:
            return
        self._next_position_publish = time.monotonic() + 1.0
        try:
            values, missing = build_position_values(self.workers, 3.0)
            if values:
                backend.write(values)
            status = (
                f"SCADA 좌표 전송 {len(values) // 8}/4대"
                + (f" / 대기: {', '.join(missing)}" if missing else " 정상")
            )
        except Exception as exc:
            # 좌표 표시 오류가 START·READY 교환과 실제 공정 제어를 중단시키지 않게
            # 분리한다. OPC 연결 자체가 끊겼다면 다음 운전 신호 읽기에서 감지된다.
            status = f"SCADA 좌표 전송 오류 - {type(exc).__name__}: {exc}"
        if status != self._last_position_status:
            self._last_position_status = status
            self.events.put(status)

    def _read_cycle_signals(self, backend, name: str, mapping: dict[str, str]) -> ProcessSignals:
        """START 수락 시 로봇별 PLC 제품·카운터 값을 읽는다."""
        values = {}
        if name == "Dobot_2":
            keys = ("input.target_count", "counter.finish_count")
            raw = backend.read([mapping[key] for key in keys])
            values = {key: raw[mapping[key]] for key in keys}
        elif name == "Dobot_4":
            key = "input.product_type"
            raw = backend.read([mapping[key]])
            values = {key: raw[mapping[key]]}
        return cycle_signals(name, values, self.product)

    def _reserve_start(
        self, backend, name: str, mapping: dict[str, str], active: ProcessSignals
    ) -> PendingStart | None:
        """마지막 조립 중의 새 START를 다음 제품 요청으로 저장한다."""
        if name == "Dobot_2":
            if active.completed_count != active.work_count - 1:
                self.events.put(
                    f"{name}: 첫 조립 중 START 무시; 두 번째 조립은 첫 FINISH 후 새 START 필요"
                )
                return None
            return PendingStart(next_dobot_2_product=True)
        return PendingStart(signals=self._read_cycle_signals(backend, name, mapping))

    @staticmethod
    def _base_supply_finish(manager, name: str) -> bool:
        if name != "Dobot_4":
            return False
        getter = getattr(manager, "dobot_4_base_supply_finish", None)
        return getter() is True if callable(getter) else False

    def _pending_signals(
        self, backend, name: str, mapping: dict[str, str], pending: PendingStart,
        *, reset_seen: bool = False,
    ) -> ProcessSignals | None:
        """PLC의 0/0 초기화 관측 후 다음 제품 목표와 완료 0으로 예약을 실행한다."""
        if pending.signals is not None:
            return pending.signals
        keys = ("input.target_count", "counter.finish_count")
        raw = backend.read([mapping[key] for key in keys])
        target, finished = (raw[mapping[key]] for key in keys)
        if (not reset_seen or type(target) is not int or type(finished) is not int
                or target not in (1, 2) or finished != 0):
            return None
        return cycle_signals(name, dict(zip(keys, (target, finished))), self.product)

    def _run(self) -> None:
        """START 읽기와 상태 쓰기를 반복하고 자동 모드에서만 공정을 접수한다."""
        backend = self.backend_factory()
        pool = ThreadPoolExecutor(max_workers=4)
        futures = {}
        edges = StartEdges()
        active_signals: dict[str, ProcessSignals] = {}
        pending_starts: dict[str, PendingStart] = {}
        pending_finished_at: dict[str, float] = {}
        pending_reset_seen: dict[str, bool] = {}

        def start_cycle(
            name: str,
            signals: ProcessSignals | None = None,
            *,
            queued: bool = False,
            base_supply: bool = False,
        ) -> None:
            admitted = False
            with self.admission_lock:
                # 수동 전환과 새 기동 접수가 동시에 발생해도 이 잠금에서 재확인한다.
                if not self.stop_requested.is_set() and self.automatic_enabled.is_set():
                    self.manager.handshakes[name].mark_running()
                    if base_supply:
                        futures[name] = pool.submit(
                            self.manager.process_dobot_4_base_supply,
                            automatic=True,
                        )
                        active_signals.pop(name, None)
                    else:
                        futures[name] = pool.submit(
                            self.manager.process, name, signals, automatic=True
                        )
                        active_signals[name] = signals
                    admitted = True
            if admitted:
                self._publish(backend, name)
                kind = "예약 START" if queued else "PLC START"
                process = "베이스 공급" if base_supply else "제품 공정"
                self.events.put(f"{name}: {kind}로 {process} 시작")

        try:
            backend.connect()
            self.connected.set()
            mode = "자동" if self.automatic_enabled.is_set() else "수동"
            self.events.put(
                f"OPC-UA 연결 완료 / {mode} 모드: START와 상태 신호를 교환합니다."
            )
            while not self.stop_requested.is_set() or futures:
                for name in ROBOT_NAMES:
                    mapping = node_map_for_robot(name)
                    handshake = self.manager.handshakes[name]
                    future = futures.get(name)
                    if future is not None and future.done():
                        del futures[name]
                        try:
                            future.result()
                        except Exception as exc:
                            self.events.put(
                                f"{name}: 자동 공정 오류 - {type(exc).__name__}: {exc}"
                            )
                        self._publish(backend, name)
                        if self.manager.plc_response(name).error:
                            pending_starts.pop(name, None)
                            pending_finished_at.pop(name, None)
                            pending_reset_seen.pop(name, None)
                        else:
                            self.events.put(f"{name}: 자동 공정 완료")
                            if name in pending_starts:
                                pending_finished_at[name] = time.monotonic()
                        future = None
                    node = mapping["command.start"]
                    input_nodes = [node]
                    if name == "Dobot_4":
                        input_nodes.append(mapping["command.base_start"])
                    raw_inputs = backend.read(input_nodes)
                    start = raw_inputs[node]
                    self.latest_start[name] = start
                    rising = edges.consume(name, start)
                    base_rising = False
                    if name == "Dobot_4":
                        base_start = raw_inputs[mapping["command.base_start"]]
                        self.latest_dobot_4_base_start = base_start
                        base_rising = edges.consume("Dobot_4.base_supply", base_start)
                    if not start:
                        handshake.set_start(False)
                    if not self.automatic_enabled.is_set() or self.stop_requested.is_set():
                        if pending_starts.pop(name, None) is not None:
                            self.events.put(f"{name}: 자동 모드 종료로 예약 START 취소")
                        pending_finished_at.pop(name, None)
                        pending_reset_seen.pop(name, None)
                        self._publish(backend, name)
                        continue
                    response = self.manager.plc_response(name)
                    pending = pending_starts.get(name)
                    if (future is None and pending is not None
                            and pending.next_dobot_2_product):
                        # 이전 FINISH 후 PLC가 0/0으로 초기화한 사실을 실제로 읽어야
                        # 같은 목표 1/완료 0인 다음 제품도 이전 제품과 구분할 수 있다.
                        keys = ("input.target_count", "counter.finish_count")
                        raw = backend.read([mapping[key] for key in keys])
                        if tuple(raw[mapping[key]] for key in keys) == (0, 0):
                            pending_reset_seen[name] = True
                    base_finish = self._base_supply_finish(self.manager, name)
                    if future is not None or response.finish or base_finish:
                        # 실행 중 또는 FINISH 펄스 중의 다음 상승 에지는 한 건만 보관한다.
                        incoming = []
                        if base_rising:
                            incoming.append("base_supply")
                        if rising:
                            incoming.append("normal")
                        for request_kind in incoming:
                            if name in pending_starts:
                                self.events.put(f"{name}: 추가 START 무시 (예약 1건 가득 참)")
                            elif not response.error:
                                try:
                                    if request_kind == "base_supply":
                                        pending = PendingStart(dobot_4_base_supply=True)
                                    else:
                                        active = active_signals.get(name)
                                        if name == "Dobot_2" and active is None:
                                            raise ValueError("Dobot_2 실행 카운터를 확인할 수 없습니다.")
                                        pending = self._reserve_start(
                                            backend, name, mapping, active
                                        )
                                except (TypeError, ValueError) as exc:
                                    self.events.put(
                                        f"{name}: 예약 START 입력 오류 - {type(exc).__name__}: {exc}"
                                    )
                                else:
                                    if pending is not None:
                                        pending_starts[name] = pending
                                        if pending.next_dobot_2_product:
                                            pending_reset_seen[name] = False
                                        if future is None:
                                            pending_finished_at[name] = time.monotonic()
                                        self.events.put(f"{name}: 다음 START 1건 예약")
                        self._publish(backend, name)
                        continue
                    if response.error:
                        pending_starts.pop(name, None)
                        pending_finished_at.pop(name, None)
                        pending_reset_seen.pop(name, None)
                        self._publish(backend, name)
                        continue
                    pending = pending_starts.get(name)
                    if pending is not None and response.ready:
                        if pending.dobot_4_base_supply:
                            pending_starts.pop(name, None)
                            pending_finished_at.pop(name, None)
                            pending_reset_seen.pop(name, None)
                            start_cycle(name, queued=True, base_supply=True)
                        elif (pending.signals is None
                                and time.monotonic() - pending_finished_at.get(name, time.monotonic()) > 30.0):
                            pending_starts.pop(name, None)
                            pending_finished_at.pop(name, None)
                            pending_reset_seen.pop(name, None)
                            self.events.put(f"{name}: 다음 제품 목표/완료 횟수 미갱신으로 예약 START 취소")
                        elif not pending.dobot_4_base_supply:
                            signals = self._pending_signals(
                                backend, name, mapping, pending,
                                reset_seen=pending_reset_seen.get(name, False),
                            )
                            if signals is not None:
                                pending_starts.pop(name, None)
                                pending_finished_at.pop(name, None)
                                pending_reset_seen.pop(name, None)
                                start_cycle(name, signals, queued=True)
                        self._publish(backend, name)
                        continue
                    if base_rising and response.ready:
                        start_cycle(name, base_supply=True)
                        if rising:
                            self.events.put(
                                "Dobot_4: 일반 START와 베이스 START가 동시에 들어와 베이스 공급을 우선 실행"
                            )
                    elif rising and response.ready:
                        try:
                            signals = self._read_cycle_signals(backend, name, mapping)
                        except (TypeError, ValueError) as exc:
                            handshake.mark_error()
                            self._publish(backend, name)
                            self.events.put(
                                f"{name}: 기동 입력 오류 - {type(exc).__name__}: {exc}"
                            )
                            continue
                        start_cycle(name, signals)
                    self._publish(backend, name)
                self._publish_positions(backend)
                # Event.wait would spin during draining after stop is requested.
                threading.Event().wait(0.25)
        except Exception as exc:
            self.stop_requested.set()
            error_name = type(exc).__name__
            error_text = str(exc)
            if error_name == "BadShutdown" or "BadShutdown" in error_text:
                self.events.put(
                    "OPC-UA 서버가 세션을 종료했습니다(BadShutdown). "
                    "KEPServerEX 상태를 확인한 뒤 'OPC-UA 연결/재연결'을 누르세요."
                )
            else:
                self.events.put(
                    f"OPC-UA 신호 교환 중단: {error_name}: {error_text}"
                )
        finally:
            self.stop_requested.set()
            self.connected.clear()
            pool.shutdown(wait=True)
            # 종료 중 FINISH ON이 PLC에 남지 않도록 네 출력 모두 OFF로 전송한다.
            self.manager.mark_plc_disconnected("OPC-UA 신호 교환 종료")
            for name in ROBOT_NAMES:
                try:
                    self._publish(backend, name)
                except Exception:
                    pass
            try:
                backend.disconnect()
            except Exception:
                pass
            self.events.put("OPC-UA 신호 교환 종료")
