"""V13.29 비전3 시트 색상 유무 판정 제어판."""
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
        self.root=root;root.title("PV5 V13.29 비전3 시트 색상 유무 제어판")
        self.base=tk.StringVar(value="http://127.0.0.1:5000")
        root.geometry("1280x850")
        self.display_mode=tk.StringVar(value="간단")
        self.stream_mode="compact"
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
        displaybox=ttk.Combobox(displaybar,textvariable=self.display_mode,values=("간단","상세","숨김"),state="readonly",width=7)
        displaybox.pack(side="left",padx=5)
        displaybox.bind("<<ComboboxSelected>>",self.change_display)
        ttk.Label(displaybar,text="창 크기에 맞춰 영상 자동 확대 · 간단 모드는 영상 아래에 정보 표시").pack(side="left")
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
            ttk.Button(row,text="검사영역 설정",command=lambda n=i:self.open_roi(n)).pack(side="left",padx=3)
            if i == 3:
                ttk.Button(row,text="시트 색상 유무 설정",command=self.open_v3_color_presence).pack(side="left",padx=3)
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

    def open_roi(self,station_id):
        endpoint=self.url(f"/api/roi/{station_id}")
        snapshot=self.url(f"/snapshot/vision{station_id}.jpg?info=raw")
        def load():
            current=self.http(endpoint)
            response=requests.get(snapshot,timeout=5);response.raise_for_status()
            with Image.open(io.BytesIO(response.content)) as decoded:frame=decoded.convert("RGB")
            return current,frame
        self.run_job("검사영역 읽기",load,
                     lambda result:self.roi_window(station_id,result[1],result[0],endpoint))

    def roi_window(self,station_id,frame,current,endpoint):
        if station_id == 3:
            return self.v3_object_roi_window(frame,current,endpoint)
        window=tk.Toplevel(self.root);window.title(f"비전{station_id} 컨베이어 검사영역")
        window.transient(self.root);window.grab_set()
        image=frame.copy();image.thumbnail((850,600),Image.Resampling.LANCZOS)
        width,height=image.size
        photo=ImageTk.PhotoImage(image);window.roi_photo=photo
        canvas=tk.Canvas(window,width=width,height=height,cursor="cross",highlightthickness=0)
        canvas.pack(padx=10,pady=10);canvas.create_image(0,0,anchor="nw",image=photo)
        roi=list(current.get("roi",[0.0,0.0,1.0,1.0]))
        saved_polygon=current.get("polygon")
        initial_points=([[float(x)*width,float(y)*height] for x,y in saved_polygon]
                        if isinstance(saved_polygon,list) and len(saved_polygon)>=3 else [])
        selection={"start":None,"coords":[roi[0]*width,roi[1]*height,roi[2]*width,roi[3]*height],
                   "points":initial_points}
        mode=tk.StringVar(value="polygon" if initial_points else "rectangle")

        def redraw():
            canvas.delete("roi_shape")
            if mode.get()=="rectangle":
                canvas.create_rectangle(*selection["coords"],outline="#00ffff",width=3,tags="roi_shape")
            else:
                points=selection["points"]
                if len(points)>=2:
                    flat=[value for point in points for value in point]
                    canvas.create_line(*flat,fill="#00ffff",width=3,tags="roi_shape")
                if len(points)>=3:
                    flat=[value for point in points for value in point]
                    canvas.create_polygon(*flat,outline="#00ffff",fill="",width=3,tags="roi_shape")
                for index,(x,y) in enumerate(points,1):
                    canvas.create_oval(x-5,y-5,x+5,y+5,fill="#ffff00",outline="#003333",tags="roi_shape")
                    canvas.create_text(x+9,y-9,text=str(index),fill="#ffff00",anchor="sw",tags="roi_shape")
        redraw()

        def press(event):
            x,y=max(0,min(width,event.x)),max(0,min(height,event.y))
            if mode.get()=="polygon":
                if len(selection["points"])<20:
                    selection["points"].append([x,y]);redraw()
            else:
                selection["start"]=(x,y)
        def drag(event):
            if mode.get()!="rectangle" or selection["start"] is None:return
            x,y=max(0,min(width,event.x)),max(0,min(height,event.y))
            selection["coords"]=[selection["start"][0],selection["start"][1],x,y];redraw()
        def release(event):
            drag(event);selection["start"]=None
        canvas.bind("<ButtonPress-1>",press);canvas.bind("<B1-Motion>",drag);canvas.bind("<ButtonRelease-1>",release)

        controls=ttk.Frame(window,padding=(10,0,10,10));controls.pack(fill="x")
        ttk.Label(controls,text="사각형은 마우스로 드래그하고, 다각형은 꼭짓점을 차례대로 클릭하세요. 마지막 점은 자동으로 첫 점과 연결됩니다.").pack(anchor="w")
        mode_row=ttk.Frame(controls);mode_row.pack(fill="x",pady=(6,0))
        ttk.Radiobutton(mode_row,text="사각형 드래그",variable=mode,value="rectangle",command=redraw).pack(side="left")
        ttk.Radiobutton(mode_row,text="다각형 점 찍기",variable=mode,value="polygon",command=redraw).pack(side="left",padx=12)

        def undo_point():
            if selection["points"]:selection["points"].pop();redraw()
        def clear_points():selection["points"].clear();redraw()
        ttk.Button(mode_row,text="마지막 점 취소",command=undo_point).pack(side="left",padx=4)
        ttk.Button(mode_row,text="점 전체 삭제",command=clear_points).pack(side="left",padx=4)
        row=ttk.Frame(controls);row.pack(fill="x",pady=6)
        ttk.Label(row,text="최소 겹침 비율").pack(side="left")
        overlap=tk.DoubleVar(value=float(current.get("min_overlap",0.70)))
        ttk.Spinbox(row,textvariable=overlap,from_=0.10,to=1.00,increment=0.05,width=6).pack(side="left",padx=5)

        def full_frame():
            mode.set("rectangle");selection["coords"]=[0,0,width,height];redraw()
        def save():
            if mode.get()=="polygon":
                if len(selection["points"])<3:
                    return messagebox.showerror("검사영역 오류","다각형은 점을 3개 이상 찍어야 합니다.",parent=window)
                payload={"polygon":[[x/width,y/height] for x,y in selection["points"]],
                         "min_overlap":overlap.get()}
            else:
                x1,y1,x2,y2=selection["coords"];x1,x2=sorted((x1,x2));y1,y2=sorted((y1,y2))
                payload={"roi":[x1/width,y1/height,x2/width,y2/height],"min_overlap":overlap.get()}
            def done(_):
                if window.winfo_exists():window.destroy()
                messagebox.showinfo("검사영역 저장 완료",f"비전{station_id}은 이제 지정한 컨베이어 영역 안의 부품만 판정합니다.")
            self.run_job("검사영역 저장",lambda:self.http(endpoint,payload,8),done,
                         lambda exc:messagebox.showerror("검사영역 저장 실패",str(exc),parent=window))
        ttk.Button(row,text="전체 화면",command=full_frame).pack(side="left",padx=8)
        ttk.Button(row,text="저장",command=save).pack(side="right",padx=4)
        ttk.Button(row,text="취소",command=window.destroy).pack(side="right",padx=4)

    def v3_object_roi_window(self,frame,current,endpoint):
        window=tk.Toplevel(self.root);window.title("비전3 객체별 다각형 ROI")
        window.transient(self.root);window.grab_set()
        image=frame.copy();image.thumbnail((900,650),Image.Resampling.LANCZOS)
        width,height=image.size;photo=ImageTk.PhotoImage(image);window.roi_photo=photo
        canvas=tk.Canvas(window,width=width,height=height,cursor="cross",highlightthickness=0)
        canvas.pack(padx=10,pady=10);canvas.create_image(0,0,anchor="nw",image=photo)
        saved=current.get("object_rois") or {}
        points={key:[[float(x)*width,float(y)*height] for x,y in ((saved.get(key) or {}).get("polygon") or [])]
                for key in ("seat1","seat2","led")}
        labels={"seat1":"시트1","seat2":"시트2","led":"LED"}
        colors={"seat1":"#ffff00","seat2":"#ff9900","led":"#ff00ff"}
        selected=tk.StringVar(value="seat1")

        def redraw():
            canvas.delete("object_roi")
            for key,vertices in points.items():
                color=colors[key]
                if len(vertices)>=2:canvas.create_line(*[v for p in vertices for v in p],fill=color,width=3,tags="object_roi")
                if len(vertices)>=3:canvas.create_polygon(*[v for p in vertices for v in p],outline=color,fill="",width=3,tags="object_roi")
                for index,(x,y) in enumerate(vertices,1):
                    canvas.create_oval(x-4,y-4,x+4,y+4,fill=color,outline="#222",tags="object_roi")
                    canvas.create_text(x+7,y-7,text=f"{labels[key]} {index}",fill=color,anchor="sw",tags="object_roi")
        redraw()
        def click(event):
            key=selected.get()
            if len(points[key])<20:
                points[key].append([max(0,min(width,event.x)),max(0,min(height,event.y))]);redraw()
        canvas.bind("<Button-1>",click)
        controls=ttk.Frame(window,padding=(10,0,10,10));controls.pack(fill="x")
        ttk.Label(controls,text="대상을 선택한 뒤 실제 부품 방향을 따라 꼭짓점을 순서대로 클릭하세요. 각 ROI는 3점 이상이어야 합니다.").pack(anchor="w")
        row=ttk.Frame(controls);row.pack(fill="x",pady=6)
        for key in ("seat1","seat2","led"):
            ttk.Radiobutton(row,text=labels[key],variable=selected,value=key).pack(side="left",padx=5)
        def undo():
            key=selected.get()
            if points[key]:points[key].pop();redraw()
        def clear():points[selected.get()].clear();redraw()
        def clear_all():
            if messagebox.askyesno("전체 ROI 삭제","시트1·시트2·LED ROI를 모두 지울까요?",parent=window):
                for value in points.values():value.clear()
                redraw()
        ttk.Button(row,text="선택 ROI 마지막 점 취소",command=undo).pack(side="left",padx=8)
        ttk.Button(row,text="선택 ROI 삭제",command=clear).pack(side="left",padx=4)
        ttk.Button(row,text="전체 ROI 삭제",command=clear_all).pack(side="left",padx=4)
        ttk.Label(row,text="최소 겹침").pack(side="left",padx=(12,2))
        overlap=tk.DoubleVar(value=float(current.get("object_min_overlap",0.25)))
        ttk.Spinbox(row,textvariable=overlap,from_=0.05,to=1.0,increment=.05,width=6).pack(side="left")
        show_rois=tk.BooleanVar(value=bool(current.get("show_object_rois",False)))
        ttk.Checkbutton(controls,text="일반 검사 화면에 ROI 외곽선 표시",variable=show_rois).pack(anchor="w",pady=(0,6))
        def save():
            invalid=[labels[key] for key,value in points.items() if 0<len(value)<3]
            if invalid:return messagebox.showerror("ROI 설정 오류",f"{', '.join(invalid)} ROI는 완전히 삭제하거나 점을 3개 이상 지정하세요.",parent=window)
            missing=[labels[key] for key,value in points.items() if not value]
            if missing and not messagebox.askyesno("미설정 ROI 확인",
                    f"{', '.join(missing)} ROI가 비어 있습니다. 해당 검사는 정상 판정할 수 없습니다. 그대로 저장할까요?",parent=window):return
            payload={"polygon":[[0,0],[1,0],[1,1],[0,1]],
                     "min_overlap":current.get("min_overlap",.70),
                     "object_rois":{key:{"polygon":[[x/width,y/height] for x,y in value]}
                                    for key,value in points.items() if value},
                     "object_min_overlap":overlap.get(),"show_object_rois":show_rois.get()}
            def done(_):
                if window.winfo_exists():window.destroy()
                messagebox.showinfo("저장 완료","비전3 ROI 편집 내용을 저장했습니다. 기존 전체 ROI는 전체 화면으로 초기화되었습니다.")
            self.run_job("비전3 ROI 저장",lambda:self.http(endpoint,payload,8),done,
                         lambda exc:messagebox.showerror("저장 실패",str(exc),parent=window))
        ttk.Button(row,text="저장",command=save).pack(side="right",padx=4)
        ttk.Button(row,text="취소",command=window.destroy).pack(side="right",padx=4)

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

        next_row=start+4
        if station_id in (2,4):
            ttk.Separator(frame,orient="horizontal").grid(row=next_row,column=0,columnspan=2,sticky="ew",pady=8)
            next_row+=1
            ttk.Label(frame,text="시트 색상 보정 (화면에 해당 시트 1개만 놓고 저장)").grid(
                row=next_row,column=0,columnspan=2,sticky="w")
            next_row+=1

            def calibrate(color):
                name="COCOA" if color=="cocoa" else "DARK"
                if not messagebox.askokcancel("색상 기준 저장",f"비전{station_id} 화면에 {name} 시트 1개만 있습니까?",parent=window):
                    return
                endpoint=self.url(f"/api/camera/{station_id}/calibrate-seat/{color}")
                def done(result):
                    readiness=("COCOA/DARK 모두 저장됨: 색상 분류 가능"
                               if result.get("ready") else "반대 색상 기준도 저장해야 분류가 시작됩니다.")
                    messagebox.showinfo("기준색 저장 완료",
                        f"{name} LAB 기준값: {result.get('lab')}\n\n{readiness}",parent=window)
                self.run_job("색상 기준 저장",lambda:self.http(endpoint,{},8),done,
                             lambda exc:messagebox.showerror("색상 보정 실패",str(exc),parent=window))

            calibration_buttons=ttk.Frame(frame)
            calibration_buttons.grid(row=next_row,column=0,columnspan=2,pady=5)
            ttk.Button(calibration_buttons,text="COCOA 기준 저장",command=lambda:calibrate("cocoa")).pack(side="left",padx=4)
            ttk.Button(calibration_buttons,text="DARK 기준 저장",command=lambda:calibrate("dark")).pack(side="left",padx=4)
            next_row+=1
            ttk.Label(frame,text="정확한 색 판정을 위해 자동 노출·자동 화이트밸런스를 끄세요.",foreground="#a05000").grid(
                row=next_row,column=0,columnspan=2,sticky="w")
            next_row+=1

        buttons=ttk.Frame(frame);buttons.grid(row=next_row,column=0,columnspan=2,pady=(10,0))
        save_button=ttk.Button(buttons,text="적용 및 저장",command=apply_and_save)
        save_button.pack(side="left",padx=4)
        ttk.Button(buttons,text="취소",command=window.destroy).pack(side="left",padx=4)

    def open_v3_color_presence(self):
        endpoint=self.url("/api/vision3/color-presence")
        self.run_job("비전3 색상 설정 읽기",lambda:self.http(endpoint,timeout=5),self.v3_color_presence_window)

    def v3_color_presence_window(self,current):
        """비전3의 좌우 시트 ROI를 색상 픽셀 비율로 판정하는 설정창입니다."""
        window=tk.Toplevel(self.root);window.title("비전3 시트 색상 유무 설정")
        window.transient(self.root);window.grab_set();window.resizable(False,False)
        frame=ttk.Frame(window,padding=12);frame.pack(fill="both",expand=True)
        settings=current.get("settings",{})
        fields={
            "seat1_min_ratio":("시트1 최소 색상 픽셀 비율(%)",1,95,float(settings.get("seat1_min_ratio",.20))*100),
            "seat2_min_ratio":("시트2 최소 색상 픽셀 비율(%)",1,95,float(settings.get("seat2_min_ratio",.20))*100),
            "color_tolerance":("색상 허용 범위",5,100,settings.get("color_tolerance",35)),
            "lightness_weight":("밝기 영향도(%)",0,100,float(settings.get("lightness_weight",.35))*100),
            "gray_margin":("회색 분리 여유",0,50,settings.get("gray_margin",4)),
        }
        variables={}
        for row,(key,(label,minimum,maximum,value)) in enumerate(fields.items()):
            ttk.Label(frame,text=label).grid(row=row,column=0,sticky="w",pady=3)
            variable=tk.StringVar(value=f"{float(value):.1f}")
            ttk.Spinbox(frame,textvariable=variable,from_=minimum,to=maximum,increment=1,width=10).grid(row=row,column=1,padx=8)
            variables[key]=variable
        row=len(fields)
        ttk.Label(frame,text="허용 범위를 높이면 조명 변화에 강해지고, 너무 높으면 회색 지그도 시트로 볼 수 있습니다.",foreground="#8a5200").grid(row=row,column=0,columnspan=3,sticky="w",pady=(8,3));row+=1
        ttk.Label(frame,text="밝기 영향도를 낮추면 밝기·그림자 변화의 영향을 덜 받습니다.",foreground="#555").grid(row=row,column=0,columnspan=3,sticky="w");row+=1
        ttk.Separator(frame,orient="horizontal").grid(row=row,column=0,columnspan=3,sticky="ew",pady=9);row+=1
        selected=tk.StringVar(value="seat1")
        ttk.Label(frame,text="보정할 검사영역").grid(row=row,column=0,sticky="w")
        ttk.Combobox(frame,textvariable=selected,values=("seat1","seat2"),state="readonly",width=10).grid(row=row,column=1,sticky="w");row+=1
        status=tk.StringVar(value="각 ROI에 시트를 놓아 COCOA/DARK를 저장하고, 비운 뒤 회색 지그를 저장하세요.")
        ttk.Label(frame,textvariable=status,wraplength=580).grid(row=row,column=0,columnspan=3,sticky="w",pady=6);row+=1

        def calibrate(kind):
            key=selected.get();names={"cocoa":"COCOA 시트","dark":"DARK 시트","gray":"빈 회색 지그"}
            if not messagebox.askokcancel("기준 저장",f"{key} ROI에 {names[kind]} 상태가 맞습니까?",parent=window):return
            endpoint=self.url(f"/api/vision3/color-presence/calibrate/{key}/{kind}")
            def done(result):
                status.set(f"{key} {names[kind]} 저장 완료: LAB {result.get('lab')} / 3종 완료: {'예' if result.get('ready') else '아니오'}")
            self.run_job("색상 기준 저장",lambda:self.http(endpoint,{},8),done,
                          lambda exc:messagebox.showerror("기준 저장 실패",str(exc),parent=window))
        buttons=ttk.Frame(frame);buttons.grid(row=row,column=0,columnspan=3,pady=5);row+=1
        ttk.Button(buttons,text="COCOA 저장",command=lambda:calibrate("cocoa")).pack(side="left",padx=3)
        ttk.Button(buttons,text="DARK 저장",command=lambda:calibrate("dark")).pack(side="left",padx=3)
        ttk.Button(buttons,text="빈 회색 지그 저장",command=lambda:calibrate("gray")).pack(side="left",padx=3)

        def save():
            try:
                payload={key:float(var.get()) for key,var in variables.items()}
                payload["seat1_min_ratio"]/=100;payload["seat2_min_ratio"]/=100;payload["lightness_weight"]/=100
            except ValueError:return messagebox.showerror("입력 확인","숫자만 입력하세요.",parent=window)
            endpoint=self.url("/api/vision3/color-presence/settings")
            def done(_):
                messagebox.showinfo("저장 완료","색상 픽셀 비율 설정을 저장했습니다. 프로그램을 재시작해도 유지됩니다.",parent=window)
                window.destroy()
            self.run_job("색상 설정 저장",lambda:self.http(endpoint,payload,8),done,
                          lambda exc:messagebox.showerror("설정 저장 실패",str(exc),parent=window))
        footer=ttk.Frame(frame);footer.grid(row=row,column=0,columnspan=3,pady=(10,0))
        ttk.Button(footer,text="설정 저장",command=save).pack(side="left",padx=4)
        ttk.Button(footer,text="닫기",command=window.destroy).pack(side="left",padx=4)

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
        self.stream_mode={"간단":"compact","상세":"detail","숨김":"none"}[self.display_mode.get()]
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
