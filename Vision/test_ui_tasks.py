import threading
import time
from ui_tasks import UiTasks

jobs=UiTasks();owner=threading.get_ident();gate=threading.Event();callbacks=[];workers=[]
def work():
    workers.append(threading.get_ident());gate.wait(2);return 42
assert jobs.submit('command',work,lambda v:callbacks.append((threading.get_ident(),v)),None)
assert not jobs.submit('command',work,None,None)
assert not callbacks
gate.set()
deadline=time.monotonic()+2
while not callbacks and time.monotonic()<deadline:
    jobs.drain();time.sleep(.005)
assert callbacks==[(owner,42)] and workers[0]!=owner
assert jobs.submit('poll',lambda:1/0,None,lambda e:callbacks.append(type(e).__name__))
deadline=time.monotonic()+2
while len(callbacks)<2 and time.monotonic()<deadline:
    jobs.drain();time.sleep(.005)
assert callbacks[-1]=='ZeroDivisionError'
gate.clear()
assert jobs.submit('command',work,lambda v:callbacks.append('late'),None)
jobs.close();gate.set();time.sleep(.05);jobs.drain()
assert 'late' not in callbacks
assert not jobs.submit('command',work,None,None)
print('PASS: nonblocking work, duplicate rejection, UI-thread callbacks, errors and close-during-request')
