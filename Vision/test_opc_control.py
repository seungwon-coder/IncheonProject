"""No physical PLC writes: lifecycle and failure regression tests."""
import asyncio
import threading
from types import SimpleNamespace
from opc_control import OpcController

class Client:
    fail=False
    def __init__(self,*args,**kwargs):self.closed=False
    async def connect(self):
        if self.fail:raise OSError('offline')
    async def disconnect(self):self.closed=True

class IO:
    fail=False
    def __init__(self,client,station,state,write,logger):self.station=station;self.write=write
    async def prepare(self):
        if self.fail:raise ValueError('bad tag')
    async def read(self,key):return False

async def main():
    started=[]
    async def cycle(io,*args):
        started.append(io.write)
        await asyncio.Event().wait()
    camera=object()
    state=SimpleNamespace(update=lambda **kwargs:None)
    renderer=SimpleNamespace(write_enabled=False)
    controller=OpcController(asyncio.get_running_loop(),{'endpoint':'opc.tcp://localhost:49320'},
        {1:{'id':1}},{1:state},{1:camera},threading.Event(),renderer,IO,cycle,lambda *a:None,Client)
    controller.submit('connect',{'endpoint':'opc.tcp://localhost:49320'})
    try:
        controller.submit('disconnect',{})
        raise AssertionError('busy command accepted')
    except ValueError:pass
    await asyncio.wrap_future(controller.command_future)
    await asyncio.sleep(0)
    assert controller.snapshot()['connected'] and started==[False]
    assert controller.cameras[1] is camera and not renderer.write_enabled
    try:
        controller.submit('connect',{'write':True})
        raise AssertionError('unconfirmed writes accepted')
    except ValueError:pass
    controller.submit('disconnect',{})
    await asyncio.wrap_future(controller.command_future)
    assert not controller.tasks and not controller.snapshot()['connected']
    assert not controller.stop.is_set()  # camera/stream remain active
    Client.fail=True
    await controller.execute('connect','opc.tcp://localhost:49320',False)
    assert not controller.snapshot()['connected'] and 'offline' in controller.snapshot()['error']
    assert not controller.stop.is_set()
    Client.fail=False;IO.fail=True
    await controller.execute('connect','opc.tcp://localhost:49320',True)
    assert not controller.tasks and not renderer.write_enabled and started==[False]
    IO.fail=False
    controller.submit('connect',{'write':True,'confirmed':True})
    await asyncio.wrap_future(controller.command_future)
    await asyncio.sleep(0)
    assert renderer.write_enabled and started==[False,True]
    await controller.shutdown()
    assert not controller.snapshot()['connected'] and not controller.tasks

asyncio.run(main())
print('PASS: read-only default, duplicate/unchecked-write rejection, failed connection/tag checks, reconnect and independent camera lifecycle')
