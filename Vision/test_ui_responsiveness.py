"""Use a real Tcl event loop with delayed HTTP stubs; no display/PLC needed."""
import sys
import types
import threading
import time
import tkinter as tk

calls=[];gate=threading.Event()
class Response:
    ok=True
    def json(self):return {'opc':{'status':'연결됨','endpoint':'test','write_enabled':False}}
    def close(self):pass

def delayed(*args,**kwargs):
    calls.append(threading.get_ident())
    gate.wait(3)
    return Response()

# All HTTP is simulated to ensure a reproducible delayed server.
sys.modules['requests']=types.SimpleNamespace(get=delayed,post=delayed,ConnectionError=ConnectionError)
import control_panel
from ui_tasks import UiTasks
root=tk.Tcl();panel=control_panel.Panel.__new__(control_panel.Panel)
panel.root=root;panel.stop=threading.Event();panel.jobs=UiTasks()
panel.base=tk.StringVar(root,value='http://127.0.0.1:5000')
panel.opc_status=tk.StringVar(root);panel.task_status=tk.StringVar(root)
panel.status_vars={i:tk.StringVar(root) for i in range(1,5)}
panel.core_process=None
owner=threading.get_ident();beats=[];notice=[]
control_panel.messagebox.showinfo=lambda *a,**k:notice.append(threading.get_ident())
control_panel.messagebox.showerror=lambda *a,**k:notice.append(threading.get_ident())
def beat():
    beats.append(time.monotonic())
    if not panel.stop.is_set():root.after(10,beat)
root.after(10,beat);panel.drain_jobs()
start=time.monotonic();panel.poll();panel.refresh()
assert time.monotonic()-start<.2, 'poll or button blocks the event loop'
panel.refresh() # repeated click must not add another command
until=time.monotonic()+1.25
while time.monotonic()<until:
    root.tk.dooneevent(2);time.sleep(.001)
assert len(beats)>70,len(beats)
assert len(calls)==2,calls  # one poll + one command; no backlog
assert all(t!=owner for t in calls)
assert not notice
max_gap=max(b-a for a,b in zip(beats,beats[1:]))
assert max_gap<.2,max_gap
gate.set()
until=time.monotonic()+1
while not notice and time.monotonic()<until:
    root.tk.dooneevent(2);time.sleep(.001)
assert notice==[owner]
assert '연결됨' in panel.opc_status.get()
panel.stop.set();panel.jobs.close()
print(f'PASS: actual panel poll/connection button delayed 1.25s; UI {len(beats)} ticks, max gap {max_gap:.3f}s; no duplicate requests; UI-only callbacks')
