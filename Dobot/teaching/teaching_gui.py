"""Dobot 4대의 저속 JOG와 현재 위치 저장을 위한 티칭 GUI."""

from __future__ import annotations

import gc
import multiprocessing as mp
import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable

from robot_control.dobot_config import ROBOT_NAMES, load_config
from robot_control.dobot_worker import DobotWorker
from robot_control.dobot_ptp import MIN_SPEED_PERCENT, MAX_SPEED_PERCENT
from robot_control.robot_motion_controller import (
    ALLOWED_DESTINATIONS,
    CYCLE_MAX_PTP_SPEED_PERCENT,
    RobotMotionController,
)
from teaching.teaching_points import load_teaching_data, save_teaching_data, teach_point


class TeachingGUI:
    """로봇 연결, 수동 이동, 좌표 저장을 한 화면에서 관리한다."""

    def __init__(self, root: tk.Misc, on_close: Callable[[], None] | None = None) -> None:
        self.root = root
        # 통합 GUI에서 보조 창으로 열었을 때 종료 사실을 부모에게 알려 준다.
        self.on_close = on_close
        root.title("Dobot 티칭 GUI")
        root.geometry("760x990")
        root.protocol("WM_DELETE_WINDOW", self.close_window)
        self.config = load_config(require_all_ports=True)
        self.points = load_teaching_data()
        self.worker: DobotWorker | None = None
        self.current_pose: dict[str, float] | None = None
        self.busy = False
        self.jog_held = False
        # Tkinter는 화면을 만든 메인 스레드에서만 종료해야 한다. 통신 스레드가
        # 남아 있는 동안 창을 먼저 없애면 Tcl_AsyncDelete 오류가 생길 수 있다.
        self.closing = False
        self.closed = False
        self._background_thread: threading.Thread | None = None
        # USB 통신 결과를 백그라운드 스레드에서 GUI로 전달하는 우편함이다.
        self.results: queue.Queue[tuple[bool, Any, Any]] = queue.Queue()
        # master를 명시하면 통합 GUI의 보조 창으로 열었을 때도 각 변수가 어느
        # Tcl 창에 속하는지 분명해져 다른 스레드에서 늦게 정리되는 일을 줄인다.
        self.robot_var = tk.StringVar(master=root, value="Dobot_1")
        self.status_var = tk.StringVar(master=root, value="연결 안 됨")
        self.point_var = tk.StringVar(master=root)
        self.speed_var = tk.DoubleVar(master=root, value=5.0)
        self.ptp_speed_var = tk.StringVar(master=root, value="5")
        self.pose_vars = {a: tk.StringVar(master=root, value="-") for a in "xyzr"}
        self.point_state_var = tk.StringVar(master=root)
        self.tool_state_var = tk.StringVar(master=root)
        self.cycle_destination_var = tk.StringVar(master=root)
        self.cycle_route_var = tk.StringVar(master=root)
        self.cycle_routes: dict[str, list[str]] = {}
        self._build()
        self._refresh_points()
        self._refresh_cycle_routes()
        root.after(100, self._poll)

    def _build(self) -> None:
        """사용 순서대로 연결, 좌표, JOG, 저장 영역을 만든다."""
        body = ttk.Frame(self.root, padding=14)
        body.pack(fill="both", expand=True)
        connect = ttk.LabelFrame(body, text="1. 로봇 연결", padding=10)
        connect.pack(fill="x", pady=5)
        ttk.Label(connect, text="로봇").grid(row=0, column=0, padx=5)
        box = ttk.Combobox(connect, textvariable=self.robot_var, values=ROBOT_NAMES,
                           state="readonly", width=12)
        box.grid(row=0, column=1, padx=5)
        box.bind("<<ComboboxSelected>>", lambda _e: self._robot_selected())
        ttk.Button(connect, text="연결", command=self.connect).grid(row=0, column=2, padx=5)
        ttk.Button(connect, text="연결 해제", command=self.disconnect).grid(row=0, column=3, padx=5)
        ttk.Button(connect, text="기계적 HOME 이동", command=self.move_home).grid(
            row=0, column=4, padx=5)
        ttk.Label(connect, textvariable=self.status_var).grid(row=0, column=5, padx=10)

        pose = ttk.LabelFrame(body, text="2. 현재 좌표", padding=10)
        pose.pack(fill="x", pady=5)
        for i, axis in enumerate("xyzr"):
            ttk.Label(pose, text=axis.upper()).grid(row=0, column=i * 2, padx=3)
            ttk.Label(pose, textvariable=self.pose_vars[axis], width=12).grid(
                row=0, column=i * 2 + 1, padx=3)
        ttk.Button(pose, text="좌표 새로고침", command=self.refresh_pose).grid(
            row=1, column=0, columnspan=8, pady=8)

        home_params = ttk.LabelFrame(
            body, text="2-1. HOME 복귀 위치 설정 (Dobot_1·Dobot_2 전용)", padding=10
        )
        home_params.pack(fill="x", pady=5)
        ttk.Button(
            home_params, text="현재 HOME 복귀 위치 읽기", command=self.read_home_params
        ).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(
            home_params,
            text="현재 자세를 HOME 복귀 위치로 저장",
            command=self.save_current_as_home_params,
        ).pack(side="left", fill="x", expand=True, padx=4)

        jog = ttk.LabelFrame(body, text="3. 연속 JOG (누르는 동안 이동)", padding=10)
        jog.pack(fill="x", pady=5)
        ttk.Label(jog, text="속도(1~100%)").grid(row=0, column=0, padx=5)
        ttk.Spinbox(jog, from_=1, to=100, textvariable=self.speed_var, width=6).grid(
            row=0, column=1, padx=5)
        ttk.Label(jog, text="직교좌표").grid(row=1, column=0, sticky="w", padx=5)
        coordinate_directions = (
            ("X+", "x", "+"), ("X-", "x", "-"),
            ("Y+", "y", "+"), ("Y-", "y", "-"),
            ("Z+", "z", "+"), ("Z-", "z", "-"),
            ("R+", "r", "+"), ("R-", "r", "-"),
        )
        for i, (label, axis, direction) in enumerate(coordinate_directions):
            button = ttk.Button(jog, text=label)
            # 누를 때 시작하고 마우스 버튼을 놓는 순간 정지한다.
            button.bind("<ButtonPress-1>",
                        lambda _e, a=axis, d=direction: self.begin_jog(a, d))
            button.bind("<ButtonRelease-1>", lambda _e: self.end_jog())
            button.grid(row=2 + i // 4, column=i % 4, padx=5, pady=5)

        ttk.Label(jog, text="관절축").grid(row=4, column=0, sticky="w", padx=5)
        joint_directions = (
            ("J1+", "j1", "+"), ("J1-", "j1", "-"),
            ("J2+", "j2", "+"), ("J2-", "j2", "-"),
            ("J3+", "j3", "+"), ("J3-", "j3", "-"),
            ("J4+", "j4", "+"), ("J4-", "j4", "-"),
        )
        for i, (label, axis, direction) in enumerate(joint_directions):
            button = ttk.Button(jog, text=label)
            # 관절축도 직교좌표와 동일하게 누를 때 시작하고 놓으면 즉시 멈춘다.
            button.bind("<ButtonPress-1>",
                        lambda _e, a=axis, d=direction: self.begin_jog(a, d))
            button.bind("<ButtonRelease-1>", lambda _e: self.end_jog())
            button.grid(row=5 + i // 4, column=i % 4, padx=5, pady=5)
        ttk.Button(jog, text="JOG 즉시 정지", command=self.stop).grid(
            row=7, column=0, columnspan=4, sticky="ew", padx=5, pady=8)

        tool = ttk.LabelFrame(body, text="4. 엔드이펙터 수동 동작", padding=10)
        tool.pack(fill="x", pady=5)
        ttk.Label(tool, textvariable=self.tool_state_var).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=5, pady=(0, 7)
        )
        self.tool_off_button = ttk.Button(
            tool, command=lambda: self.operate_tool("off")
        )
        self.tool_off_button.grid(row=1, column=0, sticky="ew", padx=5)
        self.tool_on_button = ttk.Button(
            tool, command=lambda: self.operate_tool("on")
        )
        self.tool_on_button.grid(row=1, column=1, sticky="ew", padx=5)
        ttk.Button(
            tool,
            text="엔드이펙터 제어 해제",
            command=lambda: self.operate_tool("disable"),
        ).grid(row=1, column=2, sticky="ew", padx=5)
        for column in range(3):
            tool.columnconfigure(column, weight=1)

        teach = ttk.LabelFrame(body, text="5. 티칭 포인트 저장 및 이동", padding=10)
        teach.pack(fill="both", expand=True, pady=5)
        ttk.Label(teach, text="포인트").grid(row=0, column=0, padx=5)
        self.point_box = ttk.Combobox(teach, textvariable=self.point_var,
                                      state="readonly", width=26)
        self.point_box.grid(row=0, column=1, padx=5)
        self.point_box.bind("<<ComboboxSelected>>", lambda _e: self.show_point())
        ttk.Button(teach, text="현재 위치 저장/수정", command=self.save_point).grid(
            row=0, column=2, padx=5)
        ttk.Button(
            teach,
            text="현재 위치를 사용자 READY로 저장",
            command=self.save_ready,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=5, pady=8)
        ttk.Button(
            teach,
            text="선택 포인트로 이동",
            command=self.move_to_point,
        ).grid(row=1, column=2, sticky="ew", padx=5, pady=8)
        ttk.Label(teach, text="수동 PTP 속도(5~100%)").grid(
            row=2, column=0, padx=5, sticky="w")
        ttk.Spinbox(teach, from_=MIN_SPEED_PERCENT, to=MAX_SPEED_PERCENT,
                    increment=0.1, textvariable=self.ptp_speed_var, width=6).grid(
            row=2, column=1, padx=5, sticky="w")
        ttk.Label(teach, text="다음 포인트 이동부터 적용").grid(
            row=2, column=2, padx=5, sticky="w")
        ttk.Label(teach, textvariable=self.point_state_var, wraplength=690,
                  justify="left").grid(row=3, column=0, columnspan=3,
                                        sticky="w", padx=5, pady=12)

        cycle = ttk.LabelFrame(body, text="6. 실제 1사이클 이동 포인트 순서", padding=10)
        cycle.pack(fill="x", pady=5)
        ttk.Label(cycle, text="목적지").grid(row=0, column=0, padx=5, sticky="w")
        self.cycle_destination_box = ttk.Combobox(
            cycle, textvariable=self.cycle_destination_var,
            state="readonly", width=28,
        )
        self.cycle_destination_box.grid(row=0, column=1, padx=5, sticky="w")
        self.cycle_destination_box.bind(
            "<<ComboboxSelected>>", lambda _e: self._show_cycle_route()
        )
        ttk.Label(
            cycle, textvariable=self.cycle_route_var, wraplength=690,
            justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=5, pady=(10, 2))

        self._refresh_tool_buttons()

    def _robot_selected(self) -> None:
        """로봇을 바꾸면 포인트 목록과 장착 공구 버튼을 함께 바꾼다."""
        self._refresh_points()
        self._refresh_tool_buttons()
        self._refresh_cycle_routes()

    def _refresh_cycle_routes(self) -> None:
        """실제 공정 계획에서 선택 로봇의 목적지별 이동 포인트를 가져온다."""
        robot_name = self.robot_var.get()
        controller = RobotMotionController(
            robot_name, None, config=self.config, teaching_data=self.points
        )
        destinations = sorted(ALLOWED_DESTINATIONS[robot_name])
        self.cycle_routes = {
            destination: [
                step.value
                for step in controller.build_cycle_plan(destination)
                if step.kind == "move"
            ]
            for destination in destinations
        }
        if robot_name == "Dobot_4":
            for level in ("1st", "2nd", "3rd"):
                label = f"base_supply_from_{level}"
                self.cycle_routes[label] = [
                    step.value
                    for step in controller.build_base_supply_plan(
                        f"storage_base_{level}"
                    )
                    if step.kind == "move"
                ]
            destinations = [*destinations, *(
                "base_supply_from_1st",
                "base_supply_from_2nd",
                "base_supply_from_3rd",
            )]
        self.cycle_destination_box["values"] = destinations
        current = self.cycle_destination_var.get()
        self.cycle_destination_var.set(
            current if current in self.cycle_routes else destinations[0]
        )
        self._show_cycle_route()

    def _show_cycle_route(self) -> None:
        """포인트 순서와 실제 사이클 제한을 적용한 속도를 한 줄로 표시한다."""
        robot_name = self.robot_var.get()
        destination = self.cycle_destination_var.get()
        route = self.cycle_routes.get(destination, [])
        labels = []
        for index, point_name in enumerate(route, start=1):
            saved_speed = float(
                self.points["robots"][robot_name][point_name]["speed_percent"]
            )
            cycle_speed = min(saved_speed, CYCLE_MAX_PTP_SPEED_PERCENT)
            labels.append(f"{index}. {point_name} ({cycle_speed:g}%)")
        self.cycle_route_var.set("  →  ".join(labels))

    def _refresh_tool_buttons(self) -> None:
        """robots.json의 장착 공구에 맞는 쉬운 한글 버튼 이름을 표시한다."""
        tool_type = self.config["robots"][self.robot_var.get()]["end_effector"]
        if tool_type == "gripper":
            self.tool_state_var.set("장착 공구: 그리퍼")
            self.tool_off_button.configure(text="그리퍼 열기")
            self.tool_on_button.configure(text="그리퍼 닫기")
        else:
            self.tool_state_var.set("장착 공구: 흡착컵")
            self.tool_off_button.configure(text="흡착 OFF")
            self.tool_on_button.configure(text="흡착 ON")

    def _background(self, work: Callable[[], Any], callback: Callable[[Any], None]) -> None:
        """느린 통신을 별도 스레드에서 처리해 화면 멈춤을 막는다."""
        if self.closing:
            return
        if self.busy:
            messagebox.showinfo("작업 중", "이전 명령이 끝날 때까지 기다려 주세요.")
            return
        self.busy = True
        def run() -> None:
            try:
                self.results.put((True, work(), callback))
            except Exception as exc:
                self.results.put((False, exc, None))
        # 스레드를 보관해야 창을 닫을 때 작업 종료 여부를 확인할 수 있다.
        self._background_thread = threading.Thread(target=run, daemon=False)
        self._background_thread.start()

    def _poll(self) -> None:
        # 종료가 시작된 뒤에는 Tkinter 예약 작업을 새로 만들지 않는다.
        if self.closing:
            return
        try:
            ok, value, callback = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll)
            return
        self.busy = False
        if ok:
            callback(value)
        else:
            # 명령 시간초과 시 Worker는 잘못된 늦은 응답이 다음 명령과 섞이지 않도록
            # 종료된다. 죽은 Worker를 계속 참조하면 이후 모든 버튼에서
            # 'Worker가 실행 중이 아닙니다'가 반복되므로 즉시 연결 상태를 정리한다.
            reconnect_notice = ""
            if self.worker is not None and not self.worker.is_running:
                try:
                    self.worker.close()
                except Exception:
                    pass
                self.worker = None
                self.jog_held = False
                self.status_var.set("통신 시간초과 — 다시 연결하세요")
                reconnect_notice = "\n\n안전정지 후 연결을 해제했습니다. 다시 연결해 주세요."
            messagebox.showerror(
                "Dobot 오류", f"{type(value).__name__}: {value}{reconnect_notice}"
            )
        self.root.after(100, self._poll)

    def connect(self) -> None:
        if self.worker:
            messagebox.showinfo("연결 상태", "먼저 현재 연결을 해제하세요.")
            return
        name = self.robot_var.get()
        item = self.config["robots"][name]
        worker = DobotWorker(name, item["port"], int(item["baudrate"]))
        def work() -> dict[str, Any]:
            worker.start()
            return worker.get_status()
        def done(status: dict[str, Any]) -> None:
            self.worker = worker
            self.status_var.set(f"연결됨: {name} / {item['port']}")
            self._show_pose(status["pose"])
        self._background(work, done)

    def disconnect(self) -> None:
        worker = self.worker
        if not worker:
            return
        self.worker = None
        self.status_var.set("연결 해제 중...")
        def done(_value: Any) -> None:
            self.status_var.set("연결 안 됨")
            self._show_pose(None)
        self._background(lambda: worker.close(), done)

    def move_home(self) -> None:
        """사용자가 승인하면 연결된 로봇의 기계적 HOME 절차를 실행한다."""
        worker = self._connected()
        if worker is None:
            return
        if not messagebox.askyesno(
            "기계적 HOME 이동 확인",
            f"{self.robot_var.get()}을 기계적 HOME으로 이동할까요?\n\n"
            "이동 경로의 컨베이어와 주변 장비 간섭이 없는지 확인하세요.",
        ):
            return
        self.status_var.set("기계적 HOME 이동 중...")
        def done(result: dict[str, Any]) -> None:
            self.status_var.set(f"HOME 완료: {self.robot_var.get()}")
            self._show_pose(result["pose"])
        self._background(worker.home, done)

    def _connected(self) -> DobotWorker | None:
        if not self.worker:
            messagebox.showwarning("연결 필요", "먼저 Dobot을 연결하세요.")
        return self.worker

    def _show_pose(self, pose: dict[str, float] | None) -> None:
        self.current_pose = pose
        for axis in "xyzr":
            self.pose_vars[axis].set("-" if pose is None else f"{pose[axis]:.3f}")

    def refresh_pose(self) -> None:
        worker = self._connected()
        if worker:
            self._background(worker.get_status, lambda s: self._show_pose(s["pose"]))

    def read_home_params(self) -> None:
        """이동 없이 Dobot 컨트롤러에 저장된 HOME 복귀 좌표를 확인한다."""
        worker = self._connected()
        if worker is None:
            return

        def done(result: dict[str, Any]) -> None:
            messagebox.showinfo(
                "HOME 복귀 위치",
                f"{self.robot_var.get()}에 저장된 HOME 복귀 좌표\n\n{result['pose']}",
            )

        self._background(worker.get_home_params, done)

    def save_current_as_home_params(self) -> None:
        """Dobot_1·2의 현재 자세를 다음 HOME 명령의 복귀 위치로 저장한다."""
        worker = self._connected()
        if worker is None or self.current_pose is None:
            return
        robot_name = self.robot_var.get()
        if robot_name not in {"Dobot_1", "Dobot_2"}:
            messagebox.showwarning(
                "설정 대상 제한", "HOME 복귀 위치 변경은 Dobot_1과 Dobot_2만 허용됩니다."
            )
            return
        if not messagebox.askyesno(
            "HOME 복귀 위치 변경 확인",
            f"{robot_name}의 현재 자세를 HOME 복귀 위치로 저장할까요?\n\n"
            f"좌표: {self.current_pose}\n\n"
            "이 작업은 좌표만 저장하며 지금 즉시 로봇을 움직이지 않습니다.",
        ):
            return
        typed = simpledialog.askstring(
            "로봇 이름 재확인",
            f"설정을 저장하려면 {robot_name}을 정확히 입력하세요.",
            parent=self.root,
        )
        if typed != robot_name:
            messagebox.showwarning("저장 취소", "로봇 이름이 일치하지 않아 취소했습니다.")
            return
        requested_pose = dict(self.current_pose)

        def done(result: dict[str, Any]) -> None:
            self.status_var.set(f"{robot_name}: HOME 복귀 위치 저장 완료")
            messagebox.showinfo(
                "HOME 복귀 위치 저장 완료",
                f"{robot_name} 저장 및 재조회가 완료됐습니다.\n\n{result['pose']}\n\n"
                "실제 HOME 이동은 주변 간섭 확인 후 별도로 실행하세요.",
            )

        self._background(lambda: worker.set_home_params(requested_pose), done)

    def begin_jog(self, axis: str, direction: str) -> None:
        """버튼을 누르면 JOG를 시작하고 감시 신호 반복을 예약한다."""
        worker = self._connected()
        if worker:
            speed = float(self.speed_var.get())
            self.jog_held = True
            def started(_result: dict[str, Any]) -> None:
                self.root.after(120, self._keep_jog_alive)
            self._background(lambda: worker.start_jog(axis, direction, 0.25, speed), started)

    def _keep_jog_alive(self) -> None:
        """버튼이 눌린 동안만 Worker의 0.25초 안전 감시 시간을 연장한다."""
        if self.closing:
            return
        worker = self.worker
        if not self.jog_held or worker is None:
            return
        if self.busy:
            self.root.after(20, self._keep_jog_alive)
            return
        self._background(
            lambda: worker.keepalive_jog(0.25),
            lambda _result: self.root.after(120, self._keep_jog_alive),
        )

    def end_jog(self) -> None:
        """마우스 버튼을 놓으면 연장 신호를 끊고 정지를 요청한다."""
        self.jog_held = False
        self._stop_when_ready()

    def _stop_when_ready(self) -> None:
        """진행 중인 짧은 통신이 끝나는 즉시 JOG_STOP을 보낸다."""
        if self.closing:
            return
        worker = self.worker
        if worker is None:
            return
        if self.busy:
            self.root.after(20, self._stop_when_ready)
            return
        self._background(worker.stop_jog, lambda _result: self.refresh_pose())

    def stop(self) -> None:
        self.jog_held = False
        worker = self._connected()
        if worker:
            self._stop_when_ready()

    def operate_tool(self, requested_action: str) -> None:
        """선택 로봇의 그리퍼 또는 흡착컵에 맞는 즉시 동작을 실행한다."""
        worker = self._connected()
        if worker is None:
            return
        robot_name = self.robot_var.get()
        tool_type = self.config["robots"][robot_name]["end_effector"]

        # 화면에서는 OFF/ON이라는 공통 의미를 사용하고, Worker가 이해하는
        # 그리퍼 open/close 또는 흡착컵 off/on 명령으로 여기서 변환한다.
        if requested_action == "disable":
            action = "disable"
        elif tool_type == "gripper":
            action = "open" if requested_action == "off" else "close"
        else:
            action = requested_action

        self.status_var.set(f"엔드이펙터 동작 중: {action}")

        def done(result: dict[str, Any]) -> None:
            action_text = {
                "open": "그리퍼 열림",
                "close": "그리퍼 닫힘",
                "on": "흡착 ON",
                "off": "흡착 OFF",
                "disable": "제어 해제",
            }[result["action"]]
            self.status_var.set(f"{robot_name}: {action_text}")

        self._background(
            lambda: worker.set_end_effector(tool_type, action),
            done,
        )

    def _refresh_points(self) -> None:
        names = list(self.points["robots"][self.robot_var.get()])
        self.point_box["values"] = names
        self.point_var.set(names[0])
        self.show_point()

    def show_point(self) -> None:
        point = self.points["robots"][self.robot_var.get()][self.point_var.get()]
        if point["valid"]:
            self.point_state_var.set(
                f"등록됨 | 좌표={point['pose']} | 속도={point['speed_percent']}% | "
                f"수정={point['updated_at']}")
        else:
            self.point_state_var.set("미등록 포인트입니다.")

    def save_point(self) -> None:
        if not self._connected() or self.current_pose is None:
            return
        name, point_name = self.robot_var.get(), self.point_var.get()
        if not messagebox.askyesno(
                "티칭 좌표 확인",
                f"{name}/{point_name}에 현재 좌표를 저장할까요?\n\n{self.current_pose}"):
            return
        self.points = teach_point(self.points, name, point_name, self.current_pose,
                                  float(self.speed_var.get()))
        save_teaching_data(self.points)
        self.show_point()
        messagebox.showinfo("저장 완료", f"{name}/{point_name} 좌표를 저장했습니다.")

    def save_ready(self) -> None:
        """기계적 원점 이동 없이 현재 위치를 사용자 READY 포인트로 저장한다."""
        self.point_var.set("ready")
        self.show_point()
        self.save_point()

    def move_to_point(self) -> None:
        """선택한 등록 포인트로 저속 이동하고 실제 도착 좌표를 확인한다."""
        worker = self._connected()
        if worker is None:
            return

        robot_name = self.robot_var.get()
        point_name = self.point_var.get()
        point = self.points["robots"][robot_name][point_name]
        if not point["valid"] or point["pose"] is None:
            messagebox.showwarning(
                "이동 불가",
                f"{robot_name}/{point_name} 포인트가 아직 저장되지 않았습니다.",
            )
            return

        # 이동 시작 시 값을 확정한다. 이동 중 입력 변경은 다음 명령에 적용한다.
        try:
            move_speed = float(self.ptp_speed_var.get())
            if not MIN_SPEED_PERCENT <= move_speed <= MAX_SPEED_PERCENT:
                raise ValueError
        except (ValueError, TypeError, tk.TclError):
            messagebox.showwarning("속도 입력 오류", "수동 PTP 속도는 5~100%의 숫자로 입력하세요.")
            return
        target = dict(point["pose"])
        if not messagebox.askyesno(
            "티칭 포인트 이동 확인",
            f"{robot_name}/{point_name} 포인트로 이동할까요?\n\n"
            f"목표 좌표: {target}\n"
            f"이동 속도: {move_speed:.1f}%\n\n"
            "현재 위치에서 목표까지의 이동 경로에 간섭이 없는지 확인하세요.",
        ):
            return

        self.status_var.set(f"포인트 이동 중: {point_name}")

        def work() -> dict[str, Any]:
            return worker.move_ptp(
                target,
                speed_percent=move_speed,
                arrival_timeout=90.0,
                tolerance=0.25,
                allow_large_move=True,
            )

        def done(result: dict[str, Any]) -> None:
            self.status_var.set(
                f"포인트 도착: {point_name} | PTP 요청 속도: {move_speed:g}%"
            )
            self._show_pose(result["after"])

        self._background(work, done)

    def close_window(self) -> None:
        """통신 스레드와 Worker를 정리한 뒤 메인 스레드에서 창을 닫는다."""
        if self.closing or self.closed:
            return
        self.closing = True
        self.jog_held = False
        worker = self.worker
        self.worker = None

        # 통신 스레드가 SDK 응답을 기다리는 중이라면 Worker 프로세스를 먼저
        # 종료해 대기를 풀어 준다. 여기서는 Tkinter 객체를 건드리지 않는다.
        if self._background_thread is not None and self._background_thread.is_alive():
            if worker is not None:
                try:
                    worker.terminate()
                except Exception:
                    pass
            self.root.after(50, lambda: self._finish_close(worker))
            return
        self._finish_close(worker)

    def _finish_close(self, worker: DobotWorker | None) -> None:
        """백그라운드 작업 종료를 확인하고 Tkinter 창을 최종 정리한다."""
        thread = self._background_thread
        if thread is not None and thread.is_alive():
            self.root.after(50, lambda: self._finish_close(worker))
            return
        if worker:
            try:
                worker.stop_jog()
                worker.close()
            except Exception:
                pass
        # Tkinter Variable을 창이 살아 있는 메인 스레드에서 먼저 해제한다.
        self._release_tk_variables()
        self.closed = True
        self.root.destroy()
        if self.on_close is not None:
            self.on_close()

    def _release_tk_variables(self) -> None:
        """Tcl 인터프리터 종료 전에 Python Variable 참조를 정리한다."""
        self.pose_vars.clear()
        for name in (
            "robot_var", "status_var", "point_var", "speed_var", "ptp_speed_var",
            "point_state_var", "tool_state_var", "cycle_destination_var",
            "cycle_route_var",
        ):
            setattr(self, name, None)
        # CPython에서는 대부분 즉시 정리되지만 명시적으로 한 번 수집해 두면
        # asyncua 같은 별도 스레드가 종료 시점에 대신 수집하는 일을 예방한다.
        gc.collect()


def main() -> None:
    root = tk.Tk()
    TeachingGUI(root)
    root.mainloop()


if __name__ == "__main__":
    mp.freeze_support()
    main()
