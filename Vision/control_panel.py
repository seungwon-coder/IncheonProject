"""V13.8 유지보수 제어판. 카메라 배정과 영상 설정을 한 화면에서 관리합니다."""
import io
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import requests
from PIL import Image, ImageTk
from ui_tasks import UiTasks


class Panel:
    def __init__(self, root):
        self.jobs=UiTasks()
        self.root=root;root.title("PV5 V13.8 유지보수 제어판")
        self.base=tk.StringVar(value="http://127.0.0.1:5000")
        root.geometry("1280x850")
        self.display_mode=tk.StringVar(value="작은 글씨")
        self.stream_mode="small"
        self.stream_base="http://127.0.0.1:5000"
        self.frame_lock=threading.Lock()
        self.pending_frames={};self.raw_frames={};self.drawn_sizes={}
        self.stop=threading.Event();self.labels={};self.status_vars={};self.images={}
        self.camera_vars={};self.camera_boxes={};self.devices={}
        top=ttk.Frame(root);top.pack(fill="x",padx=8,pady=6)
        ttk.Label(top,text="Vision Core 주소").pack(side="left")
        ttk.Entry(top,textvariable=self.base,width=32).pack(side="left",padx=6)
        ttk.Button(top,text="연결 확인",command=self.refresh).pack(side="left")
        ttk.Button(top,text="카메라 찾기·주소 자동 저장",command=self.find_and_autofill).pack(side="left",padx=4)
        ttk.Button(top,text="선택 전체 배정 저장",command=self.apply_all).pack(side="left")
        self.core_process = None
        self.core_log = None
        config = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8-sig"))
        self.endpoint = tk.StringVar(value=config["endpoint"])
        self.write_mode = tk.BooleanVar(value=False)
        self.opc_status = tk.StringVar(value="Core 시작 후 OPC 연결을 누르세요 (기본: 읽기 시험)")
        runbar=ttk.LabelFrame(root,text="Core / KepServer OPC-UA",padding=6)
        runbar.pack(fill="x",padx=8,pady=4)
        ttk.Button(runbar,text="Core 시작 (카메라·AI)",command=self.start_core).grid(row=0,column=0,padx=3)
        ttk.Button(runbar,text="Core 종료",command=self.stop_core).grid(row=0,column=1,padx=3)
        ttk.Button(runbar,text="실행 로그 보기",command=self.show_log).grid(row=0,column=2,padx=3)
        ttk.Label(runbar,text="KepServer 주소").grid(row=1,column=0,pady=5)
        ttk.Entry(runbar,textvariable=self.endpoint,width=40).grid(row=1,column=1,columnspan=2,sticky="ew")
        ttk.Checkbutton(runbar,text="실제 PLC 쓰기 (기본 OFF)",variable=self.write_mode).grid(row=1,column=3,padx=6)
        ttk.Button(runbar,text="OPC 연결 / 모드 적용",command=self.connect_opc).grid(row=0,column=3,padx=4)
        ttk.Button(runbar,text="OPC 해제",command=self.disconnect_opc).grid(row=0,column=4,padx=4)
        ttk.Label(runbar,textvariable=self.opc_status,wraplength=1000).grid(row=2,column=0,columnspan=5,sticky="w")
        displaybar=ttk.Frame(root);displaybar.pack(fill="x",padx=8)
        ttk.Label(displaybar,text="영상 정보").pack(side="left")
        displaybox=ttk.Combobox(displaybar,textvariable=self.display_mode,values=("작은 글씨","간단","상세","숨김"),state="readonly",width=9)
        displaybox.pack(side="left",padx=5)
        displaybox.bind("<<ComboboxSelected>>",self.change_display)
        ttk.Label(displaybar,text="창 크기에 맞춰 영상 자동 확대 · 작은 글씨는 영상 위에 정보 표시").pack(side="left")
        self.task_status=tk.StringVar(value="")
        ttk.Label(displaybar,textvariable=self.task_status,foreground="#1766a0").pack(side="left",padx=12)
        grid=ttk.Frame(root);grid.pack(fill="both",expand=True,padx=8,pady=4)
        for i in range(1,5):
            box=ttk.LabelFrame(grid,text=f"비전{i}");box.grid(row=(i-1)//2,column=(i-1)%2,padx=5,pady=5,sticky="nsew")
            viewport=tk.Frame(box,background="#171b20",height=240)
            viewport.pack(fill="both",expand=True)
            viewport.pack_propagate(False)
            label=tk.Label(viewport,text="영상 대기",anchor="center",background="#171b20",foreground="white",borderwidth=0,highlightthickness=0)
            label.pack(fill="both",expand=True)
            self.labels[i]=label;self.status_vars[i]=tk.StringVar(value="연결 대기")
            ttk.Label(box,textvariable=self.status_vars[i],wraplength=500).pack(fill="x")
            row=ttk.Frame(box);row.pack(fill="x",pady=3)
            ttk.Button(row,text="카메라 복구",command=lambda n=i:self.post(n,"recover",{})).pack(side="left")
            ttk.Button(row,text="영상 설정",command=lambda n=i:self.open_settings(n)).pack(side="left",padx=3)
            ttk.Label(row,text="선택 번호").pack(side="left",padx=(8,2))
            variable=tk.StringVar(value="")
            selector=ttk.Combobox(row,textvariable=variable,width=5,state="readonly")
            selector.pack(side="left")
            self.camera_vars[i]=variable;self.camera_boxes[i]=selector
        for n in (0,1):grid.columnconfigure(n,weight=1,uniform="visions");grid.rowconfigure(n,weight=1,uniform="visions")
        self.mapping_status=tk.StringVar(value="먼저 카메라 찾기를 누르세요. 번호 변경은 네 대를 선택한 뒤 전체 배정 저장을 사용합니다.")
        ttk.Label(root,textvariable=self.mapping_status).pack(pady=5)
        for i in range(1,5):threading.Thread(target=self.stream,args=(i,),daemon=True).start()
        self.root.after(40,self.drain_jobs)
        self.root.after(80,self.paint_frames)
        self.root.after(1000,self.poll);self.root.protocol("WM_DELETE_WINDOW",self.close)

    def drain_jobs(self):
        if self.stop.is_set():return
        try:self.jobs.drain()
        finally:
            if not self.stop.is_set():self.root.after(40,self.drain_jobs)

    def run_job(self,title,work,success=None,failure=None):
        if "command" in self.jobs.active:
            self.task_status.set("이전 요청 처리 중 — 완료 후 다시 눌러주세요")
            return False
        self.task_status.set(title+"…")
        def done(value):
            self.task_status.set("")
            if success:success(value)
        def failed(exc):
            self.task_status.set("")
            if failure:failure(exc)
            else:messagebox.showerror(title+" 실패",str(exc))
        return self.jobs.submit("command",work,done,failed)

    @staticmethod
    def http(url,payload=None,timeout=5):
        # Called only by background workers; never reads Tk variables.
        response=(requests.get(url,timeout=timeout) if payload is None else
                  requests.post(url,json=payload,timeout=timeout))
        try:
            if not response.ok:
                try:detail=response.json().get("error",response.text)
                except (ValueError,AttributeError):detail=response.text[:300]
                raise RuntimeError(detail)
            return response.json()
        finally:response.close()

    def local_base(self):
        from urllib.parse import urlsplit
        parsed=urlsplit(self.base.get())
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1","localhost") or parsed.port != 5000:
            raise ValueError("Core/OPC 조작 시 Vision Core 주소는 http://127.0.0.1:5000 으로 설정하세요")
        return self.base.get().rstrip("/")

    def start_core(self):
        try:base=self.local_base()
        except Exception as exc:return messagebox.showerror("Core 시작 실패",str(exc))
        if self.core_process and self.core_process.poll() is None:
            return messagebox.showinfo("Core 시작",f"이전에 시작한 Core PID {self.core_process.pid}가 아직 실행 중입니다. 종료 상태와 실행 로그를 확인하세요.")
        folder=Path(__file__).resolve().parent
        def work():
            try:
                response=requests.get(base+"/health",timeout=1)
            except requests.ConnectionError:
                response=None
            if response is not None:
                response.close()
                return None
            if self.stop.is_set():return None
            logs=folder/"logs";logs.mkdir(exist_ok=True)
            path=logs/(datetime.now().strftime("%Y%m%d_%H%M%S_%f")+"_core_console.log")
            env=os.environ.copy();env["PYTHONIOENCODING"]="utf-8"
            with path.open("wb") as output:
                process=subprocess.Popen(
                    [sys.executable,"-u",str(folder/"vision_core_server.py"),"--config","config.json","--no-opc"],
                    cwd=str(folder),stdout=output,stderr=subprocess.STDOUT,env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            return process,path
        def done(result):
            if result is None:
                return messagebox.showinfo("Core 시작","이미 서버가 실행 중입니다. 현재 Core 연결 상태를 확인하세요.")
            self.core_process,self.core_log=result
            self.opc_status.set("Core 시작 중 — 카메라·AI 시험 모드 / PLC 쓰기 OFF")
        self.run_job("Core 시작",work,done)

    def local_command(self,path,payload):
        try:base=self.local_base()
        except Exception as exc:return messagebox.showerror("명령 실패",str(exc))
        self.run_job("명령 전송",lambda:self.http(base+path,payload,3),
                     lambda _:self.opc_status.set("명령 접수 — 상태 갱신 대기"))

    def connect_opc(self):
        write=self.write_mode.get()
        if write and not messagebox.askyesno("실제 PLC 쓰기 확인",
            "카메라 4대, 올바른 모델·클래스, 태그 및 PLC 래더 확인을 완료했습니까?\n"
            "현재 모델 4개가 동일한 상태라면 사용하면 안 됩니다.\n"
            "확인하면 실제 PLC 태그 쓰기가 시작됩니다."):
            return
        self.local_command("/api/opc/connect",dict(endpoint=self.endpoint.get().strip(),write=write,confirmed=write))

    def disconnect_opc(self):
        if messagebox.askyesno("OPC 해제", "생산을 정지한 상태에서 해제하세요.\nPLC 출력값은 자동 초기화하지 않습니다. 해제할까요?"):
            self.local_command("/api/opc/disconnect",{})

    def stop_core(self):
        if not messagebox.askyesno("Core 종료", "카메라·AI·OPC·SCADA 영상이 모두 중지됩니다.\nPLC 출력값은 자동 초기화하지 않습니다. 종료할까요?"):
            return
        try:base=self.local_base()
        except Exception as exc:return messagebox.showerror("Core 종료 실패",str(exc))
        process=self.core_process
        def work():
            health=self.http(base+"/health",timeout=2)
            server_pid=health.get("pid")
            owned=bool(process and process.poll() is None and (server_pid is None or server_pid==process.pid))
            if process and process.poll() is None and server_pid is not None and server_pid!=process.pid:
                raise RuntimeError(f"현재 서버 PID {server_pid}가 앱에서 시작한 PID {process.pid}와 다릅니다. 대상 Core를 확인하세요.")
            self.http(base+"/api/core/stop",{},3)
            if owned:
                try:process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    return f"종료 요청은 접수됐지만 Core PID {process.pid}가 아직 실행 중입니다. 실행 로그를 확인하세요."
                return f"Core 종료 완료 (PID {process.pid}, 코드 {process.returncode})"
            deadline=time.monotonic()+12
            while time.monotonic()<deadline:
                try:self.http(base+"/health",timeout=1)
                except requests.ConnectionError:
                    return "Core 서버 연결 종료 확인. 외부 실행 프로세스의 종료 여부는 확인할 수 없습니다."
                time.sleep(.25)
            return "종료 요청은 접수됐지만 Core 서버가 아직 응답합니다. 실행 로그를 확인하세요."
        def done(result):
            self.opc_status.set(result)
            messagebox.showinfo("Core 종료 결과",result)
        self.run_job("Core 종료 확인",work,done)

    def show_log(self):
        folder=Path(__file__).resolve().parent/"logs"
        files=sorted(folder.glob("*_core_console.log"))
        path=self.core_log or (files[-1] if files else None)
        if path is None:return messagebox.showinfo("로그", "앱에서 Core를 시작하면 실행 로그가 생성됩니다.")
        window=tk.Toplevel(self.root);window.title("Core 실행 로그")
        text=tk.Text(window,width=110,height=30);text.pack(fill="both",expand=True)
        def refresh_log():
            try:
                with path.open("rb") as f:
                    f.seek(0,2);size=f.tell();f.seek(max(0,size-100000))
                    content=f.read().decode("utf-8",errors="replace")
                text.delete("1.0","end");text.insert("end",content);text.see("end")
            except Exception as exc:messagebox.showerror("로그",str(exc))
        ttk.Button(window,text="새로고침",command=refresh_log).pack();refresh_log()

    def url(self,path):return self.base.get().rstrip("/")+path
    def refresh(self):
        url=self.url("/api/system")
        self.run_job("연결 확인",lambda:self.http(url,timeout=2),
                     lambda _:messagebox.showinfo("연결","Vision Core 연결 정상"))

    def post(self,n,action,data):
        url=self.url(f"/api/camera/{n}/{action}")
        self.run_job("카메라 명령",lambda:self.http(url,data))

    def open_settings(self,station_id):
        url=self.url(f"/api/camera/{station_id}/settings")
        self.run_job("설정 읽기",lambda:self.http(url),
                     lambda current:self.settings_window(station_id,current,url))

    def settings_window(self,station_id,current,url):
        window=tk.Toplevel(self.root);window.title(f"비전{station_id} 카메라 영상 설정")
        window.transient(self.root);window.grab_set();window.resizable(False,False)
        frame=ttk.Frame(window,padding=12);frame.pack(fill="both",expand=True)
        fields={
            "focus":("초점",0,255,5,current.get("focus",100)),
            "exposure":("노출",-13,-1,1,current.get("exposure",-5)),
            "brightness":("밝기",0,255,1,current.get("brightness")),
            "gain":("게인",0,255,1,current.get("gain")),
            "white_balance":("화이트밸런스",2000,7500,100,current.get("white_balance")),
            "fps":("FPS",1,30,1,current.get("fps",15)),
        }
        values={}
        for row,(key,(label,minimum,maximum,step,value)) in enumerate(fields.items()):
            ttk.Label(frame,text=f"{label} ({minimum}~{maximum})").grid(row=row,column=0,sticky="w",pady=3)
            variable=tk.StringVar(value="" if value is None else str(value))
            ttk.Spinbox(frame,textvariable=variable,from_=minimum,to=maximum,increment=step,width=10).grid(row=row,column=1,padx=8,pady=3)
            values[key]=variable

        auto_focus=tk.BooleanVar(value=bool(current.get("autofocus",False)))
        auto_exposure=tk.BooleanVar(value=bool(current.get("auto_exposure",False)))
        auto_wb=tk.BooleanVar(value=bool(current.get("auto_white_balance",True)))
        start=len(fields)
        ttk.Checkbutton(frame,text="자동 초점",variable=auto_focus).grid(row=start,column=0,columnspan=2,sticky="w")
        ttk.Checkbutton(frame,text="자동 노출",variable=auto_exposure).grid(row=start+1,column=0,columnspan=2,sticky="w")
        ttk.Checkbutton(frame,text="자동 화이트밸런스",variable=auto_wb).grid(row=start+2,column=0,columnspan=2,sticky="w")
        ttk.Label(frame,text="빈 밝기·게인·화이트밸런스 값은 변경하지 않습니다.",foreground="#555").grid(
            row=start+3,column=0,columnspan=2,sticky="w",pady=(8,4))

        def apply_and_save():
            payload={"autofocus":auto_focus.get(),"auto_exposure":auto_exposure.get(),
                     "auto_white_balance":auto_wb.get()}
            try:
                for key,variable in values.items():
                    text=variable.get().strip()
                    if text:payload[key]=float(text) if key in ("brightness","gain") else int(text)
            except Exception as exc:
                return messagebox.showerror("설정 입력 확인",str(exc),parent=window)
            def saved(_):
                if window.winfo_exists():window.destroy()
                messagebox.showinfo("저장 완료",f"비전{station_id} 설정을 config.json에 저장했습니다.\n카메라가 재연결되므로 약 5~15초 기다리세요.")
            def failed(exc):
                if window.winfo_exists():save_button.configure(state="normal")
                messagebox.showerror("설정 적용 실패",str(exc))
            if self.run_job("설정 저장",lambda:self.http(url,payload,8),saved,failed):
                save_button.configure(state="disabled")

        buttons=ttk.Frame(frame);buttons.grid(row=start+4,column=0,columnspan=2,pady=(10,0))
        save_button=ttk.Button(buttons,text="적용 및 저장",command=apply_and_save)
        save_button.pack(side="left",padx=4)
        ttk.Button(buttons,text="취소",command=window.destroy).pack(side="left",padx=4)

    @staticmethod
    def fetch_devices(base):
        devices=Panel.http(base+"/api/cameras")
        if not isinstance(devices,list) or not devices:raise RuntimeError("현재 검색된 카메라가 없습니다")
        mapping=Panel.http(base+"/api/camera-mapping")
        return devices,mapping

    def display_devices(self,result):
        devices,mapping=result
        self.devices={int(item["index"]):item for item in devices}
        values=[str(index) for index in sorted(self.devices)]
        for station_id in range(1,5):
            self.camera_boxes[station_id].configure(values=values)
            current=mapping.get(str(station_id),{}).get("index")
            if current is not None and int(current) in self.devices:
                self.camera_vars[station_id].set(str(current))
        summary=" / ".join(f"{index}:{self.devices[index]['name']}" for index in sorted(self.devices))
        self.mapping_status.set(f"검색 완료 — {summary}")

    def find_and_autofill(self):
        base=self.base.get().rstrip("/")
        def work():
            result=self.fetch_devices(base)
            self.http(base+"/api/cameras/autofill",{},8)
            return result
        def done(result):
            self.display_devices(result)
            messagebox.showinfo("카메라 찾기 완료","현재 비전별 번호에 맞는 장치 경로를 자동 저장했습니다.\n카메라가 순서대로 다시 연결됩니다.")
        self.run_job("카메라 찾기",work,done)

    def apply_all(self):
        base=self.base.get().rstrip("/")
        if not self.devices:
            def loaded(result):
                self.display_devices(result)
                messagebox.showinfo("카메라 번호 확인","카메라 목록을 불러왔습니다. 번호 네 개를 확인한 뒤 전체 배정 저장을 다시 누르세요.")
            self.run_job("카메라 조회",lambda:self.fetch_devices(base),loaded)
            return
        try:mapping={str(i):int(self.camera_vars[i].get()) for i in range(1,5)}
        except (ValueError,KeyError):
            return messagebox.showerror("배정 확인","비전1~4의 카메라 번호를 모두 선택하세요")
        if len(set(mapping.values()))!=4:
            return messagebox.showerror("중복 배정 차단","같은 카메라 번호를 두 비전에 사용할 수 없습니다")
        lines="\n".join(f"비전{i} ← 현재 카메라 {mapping[str(i)]}" for i in range(1,5))
        if not messagebox.askyesno("전체 카메라 배정",lines+"\n\n네 대를 한 번에 저장하고 재연결할까요?"):return
        def done(_):
            self.mapping_status.set("전체 배정 저장 완료 — 재연결 중입니다. 약 5~15초 기다리세요.")
            messagebox.showinfo("배정 완료","중복 검사와 장치 경로 저장을 완료했습니다.")
        self.run_job("전체 배정",lambda:self.http(base+"/api/cameras/bind-all",{"mapping":mapping},8),done)

    def poll(self):
        if self.stop.is_set():return
        base=self.base.get().rstrip("/")
        def done(data):
            if base==self.base.get().rstrip("/"):self.display_status(data)
        def failed(exc):
            if base!=self.base.get().rstrip("/"):return
            if self.core_process and self.core_process.poll() is not None:
                self.opc_status.set(f"Core 종료됨 (코드 {self.core_process.returncode}) — 실행 로그 보기를 확인하세요")
            elif self.core_process:
                self.opc_status.set(f"Core 서버 미응답, PID {self.core_process.pid}는 실행 중 — 실행 로그 보기를 확인하세요")
            else:self.opc_status.set("Core 미연결 — Core 시작 버튼을 누르세요")
            for value in self.status_vars.values():value.set("Vision Core 연결 끊김")
        self.jobs.submit("poll",lambda:self.http(base+"/api/system",timeout=1),done,failed)
        self.root.after(1000,self.poll)

    def display_status(self,data):
        opc=data.get("opc")
        if opc:
            self.opc_status.set(f"OPC {opc.get('status','-')} | {opc.get('endpoint','')} | 실제 쓰기 {'ON' if opc.get('write_enabled') else 'OFF'} | {opc.get('error','')}")
        else:self.opc_status.set("기존 Core 연결됨 — OPC 앱 제어에는 V13.4 이상 Core가 필요합니다")
        for i in range(1,5):
            v=data.get("visions",{}).get(str(i),{});c=data.get("cameras",{}).get(str(i),{})
            self.status_vars[i].set(f"카메라 {c.get('status','-')} 번호 {c.get('index','-')} | 주문 {v.get('product','-')} | {v.get('status','-')} {v.get('outcome','-')} | {v.get('detail') or c.get('error') or ''}")

    def change_display(self,event=None):
        self.stream_mode={"작은 글씨":"small","간단":"compact","상세":"detail","숨김":"none"}[self.display_mode.get()]
        with self.frame_lock:self.pending_frames.clear()
        self.raw_frames.clear();self.drawn_sizes.clear()

    def stream(self,n):
        # Workers only receive frames. All Tk access stays on the UI thread.
        while not self.stop.is_set():
            try:
                mode,base=self.stream_mode,self.stream_base
                with requests.get(base+f"/vision{n}",params={"info":mode},stream=True,timeout=(3,10)) as response:
                    response.raise_for_status();buffer=b""
                    for chunk in response.iter_content(4096):
                        if self.stop.is_set():return
                        if mode!=self.stream_mode or base!=self.stream_base:break
                        buffer+=chunk
                        while True:
                            start=buffer.find(b"\xff\xd8");end=buffer.find(b"\xff\xd9",start+2)
                            if start<0 or end<0:break
                            with Image.open(io.BytesIO(buffer[start:end+2])) as decoded:
                                frame=decoded.convert("RGB")
                            buffer=buffer[end+2:]
                            with self.frame_lock:self.pending_frames[n]=(mode,frame)
                        if len(buffer)>8*1024*1024:buffer=b""
            except Exception:self.stop.wait(2)

    def paint_frames(self):
        if self.stop.is_set():return
        self.stream_base=self.base.get().rstrip("/")
        with self.frame_lock:
            pending=self.pending_frames;self.pending_frames={}
        fresh=set()
        for n,(mode,frame) in pending.items():
            if mode==self.stream_mode:self.raw_frames[n]=frame;fresh.add(n)
        for n,frame in self.raw_frames.items():
            label=self.labels[n]
            bounds=(max(1,label.winfo_width()-4),max(1,label.winfo_height()-4))
            if min(bounds)<4:continue
            if n not in fresh and self.drawn_sizes.get(n)==bounds:continue
            from display_layout import fitted_size
            size=fitted_size(frame.size,bounds)
            scaled=frame.resize(size,Image.Resampling.BILINEAR)
            photo=ImageTk.PhotoImage(scaled)
            self.images[n]=photo;label.configure(image=photo,text="")
            self.drawn_sizes[n]=bounds
        self.root.after(80,self.paint_frames)

    def close(self):
        self.stop.set();self.jobs.close();self.root.destroy()  # Core continues serving SCADA as in V13.3.


if __name__=="__main__":
    root=tk.Tk();Panel(root);root.mainloop()
