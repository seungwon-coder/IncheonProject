"""Dobot 4대 통합 화면: 경로 검사, 수동 실제 운전, PLC 자동 운전."""

from __future__ import annotations

import gc
import multiprocessing as mp
import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from robot_control.dobot_config import ROBOT_NAMES
from integrated_gui.integrated_gui_model import IntegratedGuiModel
from integrated_gui.integrated_robot_status import IntegratedRobotStatusService
from robot_control.live_process_manager import LiveProcessManager
from robot_control.process_state_machine import DOBOT_4_DESTINATIONS
from teaching.teaching_gui import TeachingGUI
from communication.automatic_operation import AutomaticOperation


STATE_KOREAN = {
    "disconnected": "초기화 전",
    "homing": "HOME 이동 중",
    "waiting": "공정 대기",
    "waiting_second_supply": "2차 공급 대기",
    "running": "운전 중",
    "paused": "일시정지",
    "error": "오류/비상정지",
}


class IntegratedDobotGui(tk.Tk):
    """작업자가 모의 신호와 네 로봇 상태를 한 화면에서 확인하는 창."""

    def __init__(self) -> None:
        super().__init__()
        self.model = IntegratedGuiModel()
        self.live_service: IntegratedRobotStatusService | None = None
        self.live_process: LiveProcessManager | None = None
        self.live_results: queue.Queue[tuple[bool, object]] = queue.Queue()
        self.live_busy = False
        # 종료 중에는 after 콜백과 새 통신 스레드가 다시 만들어지지 않게 한다.
        self.closing = False
        self._live_thread: threading.Thread | None = None
        self._shutdown_thread: threading.Thread | None = None
        # 실제 로봇 연결 뒤에는 수동 모드에서도 이 객체가 OPC-UA 통신을 유지한다.
        # automatic_mode_enabled는 PLC START로 실제 기동할지 여부만 나타낸다.
        self.automatic = None
        self.automatic_mode_enabled = False
        self.mode_text = tk.StringVar(master=self, value="수동 모드 — PC 입력으로 기동")
        self.opc_status_text = tk.StringVar(master=self, value="OPC-UA: 연결 안 됨")
        self.teaching_window: tk.Toplevel | None = None
        self.teaching_gui: TeachingGUI | None = None
        self.title("Dobot 4대 통합 운전 — 수동 / 자동")
        self.geometry("1120x760")
        self.minsize(980, 680)

        self.selected_robot = tk.StringVar(master=self, value=ROBOT_NAMES[0])
        self.start_signal = tk.BooleanVar(master=self)
        self.supply_complete = tk.BooleanVar(master=self)
        self.vision_ok = tk.BooleanVar(master=self)
        self.agv_complete = tk.BooleanVar(master=self)
        self.product_arrived = tk.BooleanVar(master=self)
        self.manual_assembly = tk.IntVar(master=self, value=1)
        self.product_type = tk.StringVar(master=self, value="Main")
        # 자동 모드 진입 전에 작업자가 실제 창고 수량(0~3)을 반드시 확인한다.
        self.main_stock = tk.IntVar(master=self, value=0)
        self.main_stock_text = tk.StringVar(master=self, value="미확인")
        self.main_stock_confirmed = False
        # 입력칸에 마지막으로 자동 반영한 실제 재고이다. 사용자가 이 값과 다른
        # 숫자를 입력 중이면 주기적 화면 갱신이 덮어쓰지 않는다.
        self._main_stock_synced_count: int | None = None
        self.state_labels: dict[str, ttk.Label] = {}
        self.pose_labels: dict[str, ttk.Label] = {}
        # 각 로봇이 PLC로 보내는 출력 신호를 카드 안에서 바로 확인한다.
        # START는 PLC에서 받는 입력이므로 여기에 포함하지 않는다.
        self.plc_signal_labels: dict[str, dict[str, tk.Label]] = {}

        self._build_screen()
        self.manual_controls = []
        def collect(widget):
            for child in widget.winfo_children():
                if isinstance(child, (tk.Button, ttk.Button, ttk.Checkbutton,
                                      ttk.Radiobutton, ttk.Combobox)):
                    text = str(child.cget("text")) if "text" in child.keys() else ""
                    keep = any(word in text for word in (
                        "수동 모드", "비상정지", "일시정지", "운전 재개",
                        "OPC-UA 연결 해제", "재고 확정"))
                    if isinstance(child, ttk.Combobox) and str(child.cget("textvariable")) == str(self.selected_robot):
                        keep = True
                    if not keep:
                        self.manual_controls.append((child, str(child.cget("state"))))
                collect(child)
        collect(self)
        self.protocol("WM_DELETE_WINDOW", self._close_window)
        self._load_selected_signals()
        self._refresh()
        self.after(100, self._poll_live_result)
        self.after(200, self._poll_automatic)

    def _build_screen(self) -> None:
        banner = tk.Label(
            self,
            textvariable=self.mode_text,
            bg="#8a3b12",
            fg="white",
            font=("맑은 고딕", 13, "bold"),
            pady=8,
        )
        banner.pack(fill="x")
        modes = ttk.Frame(self)
        modes.pack(fill="x", padx=12, pady=4)
        ttk.Button(modes, text="수동 모드", command=self._manual_mode).pack(side="left", padx=4)
        ttk.Button(modes, text="자동 모드 (PLC 기동)", command=self._automatic_mode).pack(side="left", padx=4)
        ttk.Label(modes, text="자동: 4대 START 감시 / Dobot_4 제품은 화면 선택값 사용").pack(side="left", padx=8)

        status = ttk.LabelFrame(self, text="4대 로봇 상태")
        status.pack(fill="x", padx=12, pady=8)
        for column, name in enumerate(ROBOT_NAMES):
            card = ttk.Frame(status, padding=10)
            card.grid(row=0, column=column, sticky="nsew")
            status.columnconfigure(column, weight=1)
            ttk.Label(card, text=name, font=("맑은 고딕", 11, "bold")).pack()
            label = ttk.Label(card, text="-")
            label.pack(pady=4)
            pose_label = ttk.Label(card, text="X/Y/Z/R: 연결 안 됨")
            pose_label.pack()
            ttk.Label(card, text="PLC 신호", font=("맑은 고딕", 9, "bold")).pack(
                pady=(7, 1)
            )
            signal_frame = ttk.Frame(card)
            signal_frame.pack()
            robot_signal_labels: dict[str, tk.Label] = {}
            signal_names = ["START", "RUNNING", "FINISH", "ERROR", "READY"]
            if name == "Dobot_4":
                signal_names.extend(("BASE START", "BASE FINISH"))
            for signal_index, signal_name in enumerate(signal_names):
                signal_label = tk.Label(
                    signal_frame,
                    text=f"{signal_name} OFF",
                    fg="#666666",
                    font=("맑은 고딕", 8, "bold"),
                    padx=3,
                )
                signal_label.grid(
                    row=signal_index // 3,
                    column=signal_index % 3,
                    sticky="w",
                )
                robot_signal_labels[signal_name] = signal_label
            self.state_labels[name] = label
            self.pose_labels[name] = pose_label
            self.plc_signal_labels[name] = robot_signal_labels

        live = ttk.Frame(status)
        live.grid(row=1, column=0, columnspan=4, pady=(2, 8))
        ttk.Button(live, text="실제 4대 연결 및 상태 읽기", command=self._connect_live).pack(
            side="left", padx=4
        )
        ttk.Button(live, text="실제 좌표 새로고침", command=self._refresh_live).pack(
            side="left", padx=4
        )
        ttk.Button(live, text="실제 4대 연결 해제", command=self._disconnect_live).pack(
            side="left", padx=4
        )
        ttk.Button(live, text="티칭 GUI 열기", command=self._open_teaching_gui).pack(
            side="left", padx=4
        )
        ttk.Label(live, text="※ 이 영역은 이동 명령을 보내지 않습니다.").pack(
            side="left", padx=10
        )

        opc = ttk.Frame(status)
        opc.grid(row=2, column=0, columnspan=4, pady=(0, 8))
        ttk.Button(opc, text="OPC-UA 연결/재연결", command=self._connect_opc).pack(
            side="left", padx=4
        )
        ttk.Button(opc, text="OPC-UA 연결 해제", command=self._disconnect_opc).pack(
            side="left", padx=4
        )
        ttk.Label(opc, textvariable=self.opc_status_text).pack(side="left", padx=10)

        body = ttk.Frame(self, padding=(12, 0))
        body.pack(fill="both", expand=True)
        left = ttk.LabelFrame(body, text="선택 로봇 및 모의 입력", padding=12)
        left.pack(side="left", fill="y", padx=(0, 8))
        right = ttk.LabelFrame(body, text="통신 및 작업 로그", padding=8)
        right.pack(side="right", fill="both", expand=True)

        ttk.Label(left, text="대상 로봇").grid(row=0, column=0, sticky="w")
        selector = ttk.Combobox(
            left, textvariable=self.selected_robot, values=ROBOT_NAMES,
            state="readonly", width=18,
        )
        selector.grid(row=0, column=1, sticky="ew", pady=4)
        selector.bind("<<ComboboxSelected>>", lambda _event: self._load_selected_signals())

        # 비전·AGV 조건은 PLC에서 먼저 판단하므로 최종 운전 GUI는 START만 모의한다.
        checks = (("PLC 로봇 START", self.start_signal),)
        for row, (text, variable) in enumerate(checks, start=1):
            ttk.Checkbutton(left, text=text, variable=variable).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=2
            )

        ttk.Label(left, text="Dobot_2 수동 조립 위치").grid(row=5, column=0, sticky="w", pady=(8, 2))
        count_frame = ttk.Frame(left)
        count_frame.grid(row=5, column=1, sticky="w")
        ttk.Radiobutton(count_frame, text="첫 번째 조립", variable=self.manual_assembly, value=1).pack(side="left")
        ttk.Radiobutton(count_frame, text="두 번째 조립", variable=self.manual_assembly, value=2).pack(side="left")

        ttk.Label(left, text="Dobot_4 제품 종류").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Combobox(
            left, textvariable=self.product_type,
            values=tuple(DOBOT_4_DESTINATIONS), state="readonly", width=16,
        ).grid(row=6, column=1, sticky="ew")

        ttk.Label(left, text="차량 하부 창고 재고").grid(row=7, column=0, sticky="w", pady=4)
        stock_frame = ttk.Frame(left)
        stock_frame.grid(row=7, column=1, sticky="ew")
        self.main_stock_spinbox = ttk.Spinbox(
            stock_frame, from_=0, to=3, textvariable=self.main_stock, width=4
        )
        self.main_stock_spinbox.pack(side="left")
        self.main_stock_button = ttk.Button(
            stock_frame, text="재고 확정", command=self._confirm_main_stock
        )
        self.main_stock_button.pack(side="left", padx=4)
        ttk.Label(left, textvariable=self.main_stock_text, foreground="#b71c1c").grid(
            row=8, column=0, columnspan=2, sticky="w"
        )

        ttk.Button(left, text="선택 로봇 HOME 경로 검사", command=self._initialize_selected).grid(
            row=9, column=0, columnspan=2, sticky="ew", pady=(14, 3)
        )
        ttk.Button(left, text="4대 전체 HOME 경로 검사", command=self._initialize_all).grid(
            row=10, column=0, columnspan=2, sticky="ew", pady=3
        )
        ttk.Button(left, text="입력 적용 및 선택 공정 경로 검사", command=self._execute_selected).grid(
            row=11, column=0, columnspan=2, sticky="ew", pady=(14, 3)
        )
        ttk.Button(left, text="입력 적용 및 전체 공정 경로 검사", command=self._execute_all).grid(
            row=12, column=0, columnspan=2, sticky="ew", pady=3
        )
        ttk.Button(left, text="선택 작업 초기화", command=self._reset_selected).grid(
            row=13, column=0, columnspan=2, sticky="ew", pady=(14, 3)
        )
        ttk.Button(left, text="전체 작업 초기화", command=self._reset_all).grid(
            row=14, column=0, columnspan=2, sticky="ew", pady=3
        )
        tk.Button(
            left, text="선택 GUI 비상정지", command=self._emergency_selected,
            bg="#c62828", fg="white",
        ).grid(row=15, column=0, columnspan=2, sticky="ew", pady=(14, 3))
        tk.Button(
            left, text="4대 전체 GUI 비상정지", command=self._emergency_all,
            bg="#8e0000", fg="white",
        ).grid(row=16, column=0, columnspan=2, sticky="ew", pady=3)

        actual = ttk.LabelFrame(left, text="실제 운전 (선택 로봇 1대)", padding=8)
        actual.grid(row=17, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        ttk.Label(
            actual,
            text="먼저 실제 4대 연결을 완료하고 주변 간섭을 확인하세요.",
            wraplength=300,
        ).pack(fill="x", pady=(0, 5))
        tk.Button(
            actual, text="선택 로봇 실제 HOME 실행", command=self._actual_home,
            bg="#d98200", fg="white",
        ).pack(fill="x", pady=3)
        tk.Button(
            actual, text="선택 로봇 PLC READY 승인", command=self._approve_selected_ready,
            bg="#2e7d32", fg="white",
        ).pack(fill="x", pady=3)
        tk.Button(
            actual, text="선택 로봇 실제 1사이클 실행", command=self._actual_cycle,
            bg="#b85c00", fg="white",
        ).pack(fill="x", pady=3)
        tk.Button(
            actual, text="선택 로봇 일시정지", command=self._actual_pause,
            bg="#455a64", fg="white",
        ).pack(fill="x", pady=3)
        tk.Button(
            actual, text="선택 로봇 운전 재개", command=self._actual_resume,
            bg="#2e7d32", fg="white",
        ).pack(fill="x", pady=3)
        tk.Button(
            actual, text="선택 로봇 실제 작업 초기화", command=self._actual_reset,
            bg="#546e7a", fg="white",
        ).pack(fill="x", pady=3)
        tk.Button(
            actual, text="선택 로봇 실제 원점복귀", command=self._actual_recover_home,
            bg="#d98200", fg="white",
        ).pack(fill="x", pady=3)
        tk.Button(
            actual, text="4대 전체 실제 작업 초기화", command=self._actual_reset_all,
            bg="#37474f", fg="white",
        ).pack(fill="x", pady=3)
        tk.Button(
            actual, text="4대 전체 동시 원점복귀", command=self._actual_recover_home_all,
            bg="#a64b00", fg="white",
        ).pack(fill="x", pady=3)

        self.log_box = ScrolledText(right, state="disabled", font=("Consolas", 10))
        self.log_box.pack(fill="both", expand=True)

    def _signal_values(self, robot_name: str) -> dict[str, object]:
        assembly = self.manual_assembly.get() if robot_name == "Dobot_2" else 1
        return {
            "start": self.start_signal.get(),
            "supply_complete": self.supply_complete.get(),
            "vision_ok": self.vision_ok.get(),
            "agv_supply_complete": self.agv_complete.get(),
            "product_arrived": self.product_arrived.get(),
            "work_count": assembly,
            "completed_count": assembly - 1 if robot_name == "Dobot_2" else None,
            "product_type": self.product_type.get(),
        }

    def _confirm_main_stock(self) -> None:
        """작업자가 확인한 차량 하부 실재고를 자동운전 관리자에 저장한다."""
        if self.live_process is None:
            messagebox.showinfo("연결 필요", "먼저 실제 4대 연결을 완료하세요.")
            return
        try:
            count = int(self.main_stock.get())
            self.live_process.set_main_inventory_count(count)
        except Exception as exc:
            messagebox.showerror("재고 입력 오류", str(exc))
            return
        self.main_stock_confirmed = True
        self._main_stock_synced_count = count
        self.model._log(f"Dobot_4 차량 하부 창고 재고 수동 변경: {count}/3")
        self._refresh_main_stock()

    def _refresh_main_stock(self) -> None:
        """현재 수량과 비어 있음/가득 참 상태를 화면에 표시한다."""
        if self.live_process is None:
            self.main_stock_text.set("미확인 — 실제 연결 후 재고를 확정하세요")
            return
        inventory = self.live_process.main_inventory
        # 사용자가 마지막 자동 표시값과 다른 숫자를 입력 중이면 그 값을 보존한다.
        # 입력을 수정하지 않은 상태에서 사이클로 재고가 바뀐 경우에만 새 실제값을
        # 입력칸에도 자동 반영한다.
        if self.main_stock_confirmed:
            try:
                entered_count = int(self.main_stock.get())
            except (tk.TclError, TypeError, ValueError):
                entered_count = None
            if entered_count == self._main_stock_synced_count:
                self.main_stock.set(inventory.count)
            self._main_stock_synced_count = inventory.count
        confirmed = "확정" if self.main_stock_confirmed else "자동모드 전 확인 필요"
        self.main_stock_text.set(
            f"현재 {inventory.count}/3 · {inventory.status_text} · {confirmed}"
        )

    def _apply_signals(self, robot_name: str) -> None:
        self.model.update_signals(robot_name, **self._signal_values(robot_name))

    def _run_safely(self, action) -> None:
        try:
            action()
        except Exception as exc:
            messagebox.showerror("Dobot 통합 GUI 오류", f"{type(exc).__name__}: {exc}")
        finally:
            self._refresh()

    def _run_live_background(self, work) -> None:
        """USB 응답을 기다리는 동안 GUI가 멈추지 않도록 별도 스레드에서 읽는다."""
        if self.closing:
            return
        if not self._manual_only():
            return
        if self.live_busy:
            messagebox.showinfo("통신 작업 중", "현재 상태 읽기가 끝날 때까지 기다려 주세요.")
            return
        self.live_busy = True

        def run() -> None:
            try:
                self.live_results.put((True, work()))
            except Exception as exc:
                self.live_results.put((False, exc))

        self._live_thread = threading.Thread(target=run, daemon=False)
        self._live_thread.start()

    def _connect_live(self) -> None:
        if self.live_process is not None:
            messagebox.showinfo("이미 연결됨", "Dobot 4대가 이미 연결돼 있습니다.")
            return

        def work():
            if self.live_service is None:
                self.live_service = IntegratedRobotStatusService()
            connected = self.live_service.connect_all()
            statuses = self.live_service.refresh_all()
            return ("connect", connected, statuses)

        self._run_live_background(work)

    def _refresh_live(self) -> None:
        if self.live_service is None or not self.live_service.connected:
            messagebox.showinfo("연결 필요", "먼저 실제 4대 연결 및 상태 읽기를 실행하세요.")
            return
        self._run_live_background(lambda: ("refresh", self.live_service.refresh_all()))

    def _disconnect_live(self) -> None:
        if self.live_service is None:
            return

        def work():
            # COM 포트를 닫기 전에 OPC 스레드가 진행 중 공정을 마치고 끝나게 한다.
            if self.automatic is not None:
                self.automatic.stop()
                if self.automatic.thread is not None:
                    self.automatic.thread.join(timeout=30.0)
                    if self.automatic.thread.is_alive():
                        raise TimeoutError("OPC-UA 작업이 끝나지 않아 Dobot 연결을 유지합니다.")
            return ("close", self.live_service.close_all())

        self._run_live_background(work)

    def _start_opc_exchange(self) -> None:
        """기존 Dobot Worker를 유지한 채 수동 OPC-UA 교환을 시작한다."""
        if self.live_process is None or (self.automatic is not None and self.automatic.active):
            return
        self.automatic = AutomaticOperation(
            self.live_process, self.product_type.get(), automatic_enabled=False
        )
        self.automatic_mode_enabled = False
        self.opc_status_text.set("OPC-UA: 연결 중")
        self.automatic.start()

    def _connect_opc(self) -> None:
        if self.live_process is None or self.live_service is None or not self.live_service.connected:
            messagebox.showinfo("Dobot 연결 필요", "먼저 '실제 4대 연결 및 상태 읽기'를 완료하세요.")
            return
        if self.automatic is not None and self.automatic.active:
            messagebox.showinfo("이미 연결됨", "OPC-UA 신호 교환이 이미 실행 중입니다.")
            return

        def work():
            return ("opc_connect", self.live_process.restore_plc_ready_after_reconnect())

        self._run_live_background(work)

    def _disconnect_opc(self) -> None:
        if self.live_busy:
            messagebox.showinfo("통신 작업 중", "현재 작업이 끝난 뒤 다시 시도하세요.")
            return
        operation = self.automatic
        if operation is None or not operation.active:
            self.opc_status_text.set("OPC-UA: 연결 안 됨")
            return
        # PLC START의 새 접수를 즉시 막고, 진행 중인 사이클이 끝난 뒤 세션을 닫는다.
        self._manual_mode()

        def work():
            operation.stop()
            if operation.thread is not None:
                operation.thread.join(timeout=30.0)
                if operation.thread.is_alive():
                    raise TimeoutError("OPC-UA 작업이 끝나지 않아 연결 해제를 기다려야 합니다.")
            return ("opc_close",)

        self.opc_status_text.set("OPC-UA: 연결 해제 중")
        self._run_live_background(work)

    def _open_teaching_gui(self) -> None:
        """기존 티칭 기능을 통합 화면의 보조 창으로 연다.

        상태 조회 Worker와 티칭 Worker가 같은 COM 포트를 동시에 열지 못하도록
        실제 상태 연결이 해제된 경우에만 허용한다.
        """
        if self.live_busy:
            messagebox.showwarning(
                "통신 작업 중", "상태 읽기 또는 연결 해제가 끝난 뒤 다시 시도하세요."
            )
            return
        if self.live_service is not None and self.live_service.connected:
            messagebox.showwarning(
                "연결 해제 필요",
                "티칭 GUI를 열기 전에 '실제 4대 연결 해제'를 실행하세요.\n\n"
                "같은 COM 포트를 두 Worker가 동시에 사용할 수 없습니다.",
            )
            return
        if self.teaching_window is not None and self.teaching_window.winfo_exists():
            self.teaching_window.lift()
            self.teaching_window.focus_force()
            return

        self.teaching_window = tk.Toplevel(self)
        self.teaching_gui = TeachingGUI(
            self.teaching_window, on_close=self._teaching_closed
        )
        self.model._log("통합 화면에서 티칭 GUI를 열었습니다.")
        self._refresh()

    def _teaching_closed(self) -> None:
        self.teaching_window = None
        self.teaching_gui = None
        self.model._log("티칭 GUI를 닫았습니다.")
        self._refresh()

    def _poll_live_result(self) -> None:
        if self.closing:
            return
        try:
            ok, value = self.live_results.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_live_result)
            return
        self.live_busy = False
        if not ok:
            self.model._log(f"실제 Dobot 통신 오류: {type(value).__name__}: {value}")
            messagebox.showerror("실제 Dobot 통신 오류", f"{type(value).__name__}: {value}")
        else:
            action = value[0]
            if action == "connect":
                results = value[1]
                for name, result in results.items():
                    self.model._log(
                        f"{name}: 실제 연결 {'성공' if result.ok else '실패 - ' + result.error}"
                    )
                # 네 Worker가 모두 연결된 경우에만 실제 공정 관리자를 준비한다.
                if all(result.ok for result in results.values()):
                    self.live_process = LiveProcessManager(self.live_service.fleet)
                    # 저장된 수량은 보여주되, 매 실행 시 작업자가 실제 재고를 다시
                    # 확인해야 자동 모드로 들어갈 수 있다.
                    self.main_stock_confirmed = False
                    self.main_stock.set(self.live_process.main_inventory.count)
                    self._main_stock_synced_count = self.live_process.main_inventory.count
                    self.model._log("실제 공정 관리자 준비 완료 (이동 전 사용자 확인 필요)")
                    # 기존 운전 방식대로 최초 연결 시 수동 OPC-UA 교환도 시작한다.
                    self._start_opc_exchange()
            elif action == "opc_connect":
                for name, result in value[1].items():
                    self.model._log(f"{name}: OPC 재연결 {result}")
                self._start_opc_exchange()
            elif action == "opc_close":
                self.automatic = None
                self.automatic_mode_enabled = False
                self.opc_status_text.set("OPC-UA: 연결 안 됨")
                self.model._log("OPC-UA 연결만 해제했습니다. Dobot 연결은 유지합니다.")
            elif action == "refresh":
                self.model._log("실제 Dobot 4대 좌표와 알람을 새로 읽었습니다.")
            elif action == "close":
                self.live_process = None
                self.main_stock_confirmed = False
                self._main_stock_synced_count = None
                self.automatic = None
                self.automatic_mode_enabled = False
                self.opc_status_text.set("OPC-UA: 연결 안 됨")
                self.model._log("실제 Dobot 4대 연결을 해제했습니다.")
            elif action == "actual_home":
                self.model._log(f"{value[1]}: 실제 HOME 이동 완료 / PLC READY 승인 대기")
            elif action == "approve_ready":
                self.model._log(f"{value[1]}: 작업자 승인 / PLC READY ON (좌표 확인 생략)")
            elif action == "actual_cycle":
                if value[2] is None:
                    self.model._log(f"{value[1]}: 실제 기동 인터록 미충족")
                else:
                    self.model._log(f"{value[1]}: 실제 1사이클 완료")
            elif action == "actual_recover_home":
                self.model._log(f"{value[1]}: 실제 원점복귀 및 작업 초기화 완료")
            elif action == "actual_recover_home_all":
                self.model._log("Dobot 4대: 동시 원점복귀 및 작업 초기화 완료")
            self._show_live_status()
        self._refresh()
        self.after(100, self._poll_live_result)

    def _show_live_status(self) -> None:
        if self.live_service is None:
            return
        for name, status in self.live_service.statuses.items():
            if status.pose:
                pose = status.pose
                alarm = status.alarms or []
                self.pose_labels[name].configure(
                    text=(
                        f"X {pose['x']:.1f} / Y {pose['y']:.1f} / "
                        f"Z {pose['z']:.1f} / R {pose['r']:.1f}\n"
                        f"통신 연결 / 알람: {alarm or '없음'}"
                    )
                )
            elif status.error:
                self.pose_labels[name].configure(text=f"통신 실패\n{status.error}")
            else:
                self.pose_labels[name].configure(text="X/Y/Z/R: 연결 안 됨")

    def _confirm_actual_motion(self, action_text: str) -> str | None:
        """실제 이동 직전에 선택 로봇 이름을 직접 입력받아 오조작을 막는다."""
        if not self._manual_only():
            return None
        name = self.selected_robot.get()
        if self.live_process is None:
            messagebox.showwarning(
                "실제 연결 필요",
                "먼저 '실제 4대 연결 및 상태 읽기'를 성공시켜 주세요.",
            )
            return None
        if not messagebox.askyesno(
            "실제 로봇 이동 확인",
            f"{name}의 {action_text}을 실제로 실행합니다.\n\n"
            "사람, 컨베이어 및 주변 장비와의 간섭이 없는지 확인했습니까?",
        ):
            return None
        typed = simpledialog.askstring(
            "로봇 이름 재확인",
            f"실행하려면 {name}을 정확히 입력하세요.",
            parent=self,
        )
        if typed != name:
            messagebox.showwarning("실행 취소", "로봇 이름이 일치하지 않아 취소했습니다.")
            return None
        return name

    def _actual_home(self) -> None:
        name = self._confirm_actual_motion("기계적 HOME → 사용자 READY 이동")
        if name is None:
            return
        self.model._log(f"{name}: 실제 HOME 실행 요청")
        self._run_live_background(
            lambda: ("actual_home", name, self.live_process.initialize(name))
        )

    def _approve_selected_ready(self) -> None:
        """작업자 판단으로 선택 로봇의 PLC READY 출력을 승인한다."""
        if not self._manual_only():
            return
        if self.live_process is None or self.live_service is None or not self.live_service.connected:
            messagebox.showinfo("Dobot 연결 필요", "먼저 실제 4대 연결을 완료하세요.")
            return
        if self.automatic is None or not self.automatic.connected.is_set():
            messagebox.showinfo("OPC-UA 연결 필요", "READY 신호를 보내려면 OPC-UA를 연결하세요.")
            return
        name = self.selected_robot.get()
        self._run_live_background(
            lambda: ("approve_ready", name, self.live_process.approve_ready(name))
        )

    def _actual_cycle(self) -> None:
        selected = self.selected_robot.get()
        action = "1사이클 공정"
        if selected == "Dobot_2":
            action += f" ({'첫 번째' if self.manual_assembly.get() == 1 else '두 번째'} 조립)"
        name = self._confirm_actual_motion(action)
        if name is None:
            return
        # 화면의 현재 모의 입력을 실제 상태 머신의 인터록 입력으로 재사용한다.
        self._apply_signals(name)
        signals = self.model.signal_snapshot(name)
        self.model._log(f"{name}: 실제 1사이클 실행 요청")
        self._run_live_background(
            lambda: ("actual_cycle", name, self.live_process.process(name, signals))
        )

    def _actual_pause(self) -> None:
        """실제 공정 중인 선택 로봇을 다음 안전 단계 경계에서 멈춘다."""
        name = self.selected_robot.get()
        if self.live_process is None:
            messagebox.showwarning(
                "실제 연결 필요",
                "먼저 '실제 4대 연결 및 상태 읽기'를 성공시켜 주세요.",
            )
            return
        try:
            # pause()는 로봇을 새 위치로 이동시키지 않는다. 현재 PTP 한 구간이
            # 끝난 뒤 다음 단계로 넘어가지 않도록 상태만 즉시 변경한다.
            self.live_process.pause(name)
        except Exception as exc:
            messagebox.showerror("Dobot 일시정지 오류", f"{type(exc).__name__}: {exc}")
            return
        self.model._log(f"{name}: 실제 일시정지 요청 완료")
        self._refresh()

    def _actual_resume(self) -> None:
        """일시정지된 선택 로봇이 중단했던 공정의 다음 단계를 계속 실행한다."""
        name = self.selected_robot.get()
        if self.live_process is None:
            messagebox.showwarning(
                "실제 연결 필요",
                "먼저 '실제 4대 연결 및 상태 읽기'를 성공시켜 주세요.",
            )
            return
        try:
            # 새 사이클을 다시 시작하는 명령이 아니다. pause()에서 대기 중인
            # 기존 사이클만 풀어 주므로 이미 끝난 단계를 중복 실행하지 않는다.
            self.live_process.resume(name)
        except Exception as exc:
            messagebox.showerror("Dobot 운전 재개 오류", f"{type(exc).__name__}: {exc}")
            return
        self.model._log(f"{name}: 실제 운전 재개 요청 완료")
        self._refresh()

    def _actual_reset(self) -> None:
        """이동 없이 선택 로봇의 실제·모의 공정 상태와 입력 신호를 초기화한다."""
        if not self._manual_only():
            return
        name = self.selected_robot.get()
        if self.live_process is None:
            messagebox.showwarning("실제 연결 필요", "먼저 실제 4대 연결을 완료하세요.")
            return
        if not messagebox.askyesno(
            "실제 작업 초기화 확인",
            f"{name}의 공정 단계, 일시정지 및 입력 신호를 초기화할까요?\n\n"
            "이 기능은 로봇을 이동시키지 않습니다.",
        ):
            return
        try:
            self.live_process.reset(name)
            self.model.reset(name)
            self._load_selected_signals()
            self.model._log(f"{name}: 실제·모의 작업 상태 초기화 완료 (이동 없음)")
        except Exception as exc:
            messagebox.showerror("Dobot 초기화 오류", f"{type(exc).__name__}: {exc}")
        self._refresh()

    def _actual_recover_home(self) -> None:
        """확인 후 선택 로봇을 기계적 HOME과 사용자 READY로 복귀시킨다."""
        name = self._confirm_actual_motion(
            "작업 정지·초기화 후 기계적 HOME → 사용자 READY 복귀"
        )
        if name is None:
            return
        self.model._log(f"{name}: 실제 원점복귀 및 작업 초기화 요청")

        def work():
            result = self.live_process.recover_to_home(name, mechanical_home=True)
            # 실제 상태가 정상 대기로 복귀한 뒤 화면의 모의 신호도 함께 지운다.
            self.model.reset(name)
            return ("actual_recover_home", name, result)

        self._run_live_background(work)

    def _actual_reset_all(self) -> None:
        """이동 없이 실제·모의 공정 네 개와 모든 입력 신호를 초기화한다."""
        if not self._manual_only():
            return
        if self.live_process is None:
            messagebox.showwarning("실제 연결 필요", "먼저 실제 4대 연결을 완료하세요.")
            return
        if not messagebox.askyesno(
            "4대 전체 작업 초기화 확인",
            "Dobot 4대의 공정 상태와 입력 신호를 모두 초기화할까요?\n\n"
            "이 기능은 로봇을 이동시키지 않습니다.",
        ):
            return
        try:
            self.live_process.reset_all()
            self.model.reset_all()
            self._load_selected_signals()
            self.model._log("Dobot 4대: 실제·모의 작업 상태 초기화 완료 (이동 없음)")
        except Exception as exc:
            messagebox.showerror("Dobot 전체 초기화 오류", f"{type(exc).__name__}: {exc}")
        self._refresh()

    def _actual_recover_home_all(self) -> None:
        """강한 재확인 후 독립 Worker 네 개로 동시에 HOME 복귀시킨다."""
        if not self._manual_only():
            return
        if self.live_process is None:
            messagebox.showwarning("실제 연결 필요", "먼저 실제 4대 연결을 완료하세요.")
            return
        if not messagebox.askyesno(
            "4대 전체 실제 원점복귀 확인",
            "Dobot 4대의 원점복귀를 동시에 시작합니다.\n\n"
            "네 로봇 모두의 이동 경로와 로봇 간·주변 설비 간섭을 확인했습니까?",
        ):
            return
        typed = simpledialog.askstring(
            "전체 원점복귀 재확인",
            "실행하려면 '4대 전체'를 정확히 입력하세요.",
            parent=self,
        )
        if typed != "4대 전체":
            messagebox.showwarning("실행 취소", "확인 문구가 일치하지 않아 취소했습니다.")
            return
        self.model._log("Dobot 4대: 동시 원점복귀 및 작업 초기화 요청")

        def work():
            result = self.live_process.recover_all_to_home(mechanical_home=True)
            self.model.reset_all()
            return ("actual_recover_home_all", result)

        self._run_live_background(work)

    def _close_window(self) -> None:
        """창을 닫을 때 열린 Worker와 COM 포트를 먼저 정리한다."""
        if self.closing:
            return
        self.closing = True
        if self.automatic is not None:
            self.automatic.stop()
        if self.teaching_gui is not None:
            # 이 함수 안에서 JOG 정지와 티칭용 Worker 종료가 먼저 수행된다.
            self.teaching_gui.close_window()

        def shutdown() -> None:
            # 종료 작업에서는 Tkinter 객체를 만지지 않는다. 로봇 정지와 COM 포트
            # 해제만 수행하고 실제 창 파괴는 메인 스레드의 _poll_shutdown이 한다.
            if self.automatic is not None and self.automatic.thread is not None:
                # OPC 스레드가 Worker를 사용 중일 수 있으므로 먼저 종료를 기다린다.
                self.automatic.thread.join(timeout=30.0)
            if self.live_process is not None:
                for name in ROBOT_NAMES:
                    try:
                        self.live_process.emergency_stop(name)
                    except Exception:
                        pass
            if self.live_service is not None:
                try:
                    self.live_service.close_all()
                except Exception:
                    pass

        self._shutdown_thread = threading.Thread(target=shutdown, daemon=False)
        self._shutdown_thread.start()
        self.after(50, self._poll_shutdown)

    def _poll_shutdown(self) -> None:
        """모든 통신 스레드가 끝난 뒤에만 Tkinter를 종료한다."""
        live_running = self._live_thread is not None and self._live_thread.is_alive()
        shutdown_running = (
            self._shutdown_thread is not None and self._shutdown_thread.is_alive()
        )
        teaching_running = (
            self.teaching_gui is not None and not self.teaching_gui.closed
        )
        if live_running or shutdown_running or teaching_running:
            self.after(50, self._poll_shutdown)
            return
        self.destroy()

    def destroy(self) -> None:
        """Tkinter 변수를 메인 스레드에서 해제한 뒤 창을 파괴한다."""
        for name in (
            "mode_text", "opc_status_text", "selected_robot", "start_signal", "supply_complete",
            "vision_ok", "agv_complete", "product_arrived", "manual_assembly",
            "product_type", "main_stock", "main_stock_text",
        ):
            if hasattr(self, name):
                setattr(self, name, None)
        gc.collect()
        super().destroy()

    def _initialize_selected(self) -> None:
        self._run_safely(lambda: self.model.initialize(self.selected_robot.get()))

    def _initialize_all(self) -> None:
        self._run_safely(self.model.initialize_all)

    def _execute_selected(self) -> None:
        if not self._manual_only():
            return
        def action() -> None:
            name = self.selected_robot.get()
            self._apply_signals(name)
            self.model.execute(name)
        self._run_safely(action)

    def _execute_all(self) -> None:
        if not self._manual_only():
            return
        def action() -> None:
            for name in ROBOT_NAMES:
                self._apply_signals(name)
            self.model.execute_all()
        self._run_safely(action)

    def _reset_selected(self) -> None:
        self._run_safely(lambda: self.model.reset(self.selected_robot.get()))
        self._load_selected_signals()

    def _reset_all(self) -> None:
        self._run_safely(self.model.reset_all)
        self._load_selected_signals()

    def _emergency_selected(self) -> None:
        name = self.selected_robot.get()
        self._run_safely(lambda: self.model.emergency_stop(name))
        self._request_actual_emergency((name,))

    def _emergency_all(self) -> None:
        self._run_safely(self.model.emergency_stop_all)
        self._request_actual_emergency(ROBOT_NAMES)

    def _request_actual_emergency(self, robot_names) -> None:
        """일반 명령 통신과 별개로 실제 Worker의 강제 정지 경로를 요청한다."""
        if self.automatic is not None:
            # OPC 연결은 유지해 ERROR를 PLC에 보낼 수 있게 하고 자동 기동만 막는다.
            self.automatic.set_automatic(False)
        self.automatic_mode_enabled = False
        if self.live_process is None:
            return

        def stop_now() -> None:
            for name in robot_names:
                try:
                    self.live_process.emergency_stop(name)
                except Exception as exc:
                    self.model._log(f"{name}: 실제 비상정지 오류 - {exc}")

        threading.Thread(target=stop_now, daemon=True).start()

    def _manual_only(self) -> bool:
        if self.automatic_mode_enabled:
            messagebox.showinfo("자동 모드", "수동 모드로 전환하고 현재 공정이 끝난 뒤 실행하세요.")
            return False
        return True

    def _manual_mode(self) -> None:
        if self.automatic is not None and self.automatic.active:
            self.automatic.set_automatic(False)
        self.automatic_mode_enabled = False
        self.mode_text.set("수동 모드 — OPC 통신 유지 / PLC START는 표시만")
        self.model._log("수동 모드 선택: OPC 통신은 유지하고 PLC START 기동은 차단합니다.")

    def _automatic_mode(self) -> None:
        if self.automatic_mode_enabled:
            return
        if self.live_busy or self.live_process is None:
            messagebox.showinfo("준비 필요", "실제 로봇 연결과 READY 승인을 먼저 완료하세요.")
            return
        if any(self.live_process.state(name) not in ("waiting", "waiting_second_supply")
               or not self.live_process.plc_response(name).ready for name in ROBOT_NAMES):
            messagebox.showinfo("준비 필요", "4대 모두 작업자 PLC READY 승인이 필요합니다.")
            return
        if self.automatic is None or not self.automatic.connected.is_set():
            messagebox.showinfo(
                "OPC-UA 연결 필요",
                "'OPC-UA 연결/재연결'을 눌러 통신을 먼저 연결하세요.",
            )
            return
        if not self.main_stock_confirmed:
            messagebox.showinfo(
                "재고 확인 필요",
                "자동 모드로 전환하기 전에 차량 하부 창고의 실제 재고(0~3)를 "
                "입력하고 '재고 확정'을 눌러 주세요.",
            )
            return
        self.automatic.set_automatic(True, self.product_type.get())
        self.automatic_mode_enabled = True
        self.mode_text.set(
            "자동 모드 — PLC START로 실제 기동 / "
            "Dobot_4: VISION4_CLASS_RESULT(2~6) 자동 선택"
        )
        self.model._log("자동 모드 선택: START OFF 이후 새 ON부터 기동합니다.")

    def _poll_automatic(self) -> None:
        if self.closing:
            return
        if self.automatic is not None:
            while True:
                try:
                    self.model._log(self.automatic.events.get_nowait())
                except queue.Empty:
                    break
            if not self.automatic.active:
                self.mode_text.set("수동 모드 — OPC-UA 통신 종료")
                self.opc_status_text.set("OPC-UA: 연결 안 됨")
                self.automatic = None
                self.automatic_mode_enabled = False
            elif self.automatic.stop_requested.is_set():
                self.opc_status_text.set("OPC-UA: 연결 해제 중")
            elif self.automatic.connected.is_set():
                self.opc_status_text.set("OPC-UA: 연결됨")
            self._refresh()
        active = self.automatic_mode_enabled
        for widget, original_state in self.manual_controls:
            widget.configure(state="disabled" if active else original_state)
        self.after(200, self._poll_automatic)

    def _load_selected_signals(self) -> None:
        signal = self.model.signal_snapshot(self.selected_robot.get())
        self.start_signal.set(signal.start)
        self.supply_complete.set(signal.supply_complete)
        self.vision_ok.set(signal.vision_ok)
        self.agv_complete.set(signal.agv_supply_complete)
        self.product_arrived.set(signal.product_arrived)
        if self.selected_robot.get() == "Dobot_2":
            self.manual_assembly.set(2 if signal.completed_count == 1 else 1)
        self.product_type.set(signal.product_type or "Main")

    def _refresh(self) -> None:
        for name, label in self.state_labels.items():
            # 실제 공정 관리자가 HOME/운전 상태를 가지고 있으면 그 상태를 우선
            # 표시하고, 아직 실제 초기화 전이면 DRY-RUN 상태를 표시한다.
            state = self.model.state(name)
            if self.live_process is not None:
                live_state = self.live_process.state(name)
                if live_state != "disconnected":
                    state = live_state
            label.configure(text=STATE_KOREAN.get(state, state))
        self._refresh_main_stock()
        self._refresh_plc_signal_labels()
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("end", "\n".join(self.model.logs))
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _refresh_plc_signal_labels(self) -> None:
        """로봇별 PLC START 입력과 출력 네 개를 ON/OFF 색상으로 갱신한다.

        실제 공정 관리자가 준비되기 전에는 아직 PLC로 보낼 준비 상태가 아니므로
        네 신호를 모두 OFF로 표시한다. 작업자가 READY를 승인하면
        LiveProcessManager의 핸드셰이크 값에 따라 READY가 ON으로 바뀐다.
        """
        on_colors = {
            "START": "#ef6c00",
            "RUNNING": "#1565c0",
            "FINISH": "#6a1b9a",
            "ERROR": "#c62828",
            "READY": "#2e7d32",
            "BASE START": "#ef6c00",
            "BASE FINISH": "#6a1b9a",
        }
        for robot_name, labels in self.plc_signal_labels.items():
            values = {
                "START": False,
                "RUNNING": False,
                "FINISH": False,
                "ERROR": False,
                "READY": False,
            }
            if robot_name == "Dobot_4":
                values.update({"BASE START": False, "BASE FINISH": False})
            if self.live_process is not None:
                response = self.live_process.plc_response(robot_name)
                values.update(
                    RUNNING=response.running,
                    FINISH=response.finish,
                    ERROR=response.error,
                    READY=response.ready,
                )
                if robot_name == "Dobot_4":
                    values["BASE FINISH"] = (
                        self.live_process.dobot_4_base_supply_finish() is True
                    )
            if self.automatic is not None:
                start_value = self.automatic.latest_start.get(robot_name)
                if isinstance(start_value, bool):
                    values["START"] = start_value
                if robot_name == "Dobot_4" and isinstance(
                    self.automatic.latest_dobot_4_base_start, bool
                ):
                    values["BASE START"] = (
                        self.automatic.latest_dobot_4_base_start
                    )
            for signal_name, signal_label in labels.items():
                is_on = values[signal_name]
                signal_label.configure(
                    text=f"{signal_name} {'ON' if is_on else 'OFF'}",
                    fg=on_colors[signal_name] if is_on else "#666666",
                )


if __name__ == "__main__":
    mp.freeze_support()
    IntegratedDobotGui().mainloop()
