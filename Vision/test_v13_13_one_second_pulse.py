import asyncio
import threading
import time
import numpy as np

from vision_core_server import Detection, OpcIO, station_cycle


class State:
    def __init__(self):self.values={}
    def update(self,**values):self.values.update(values)


class Camera:
    def __init__(self):self.seq=0
    def get(self):
        self.seq+=1
        frame=np.zeros((480,640,3),dtype=np.uint8)
        return (self.seq,time.monotonic(),frame,[Detection("car lamp_R","round",.99,[10,10,100,100],None)]),None


class FakeIO:
    def __init__(self,stop):
        self.station={"id":1,"inspection":"lamp","inspection_roi":[0,0,1,1],"roi_min_overlap":.7}
        self.state=State();self.stop=stop;self.events=[];self.start_reads=0
        self.output_high=False;self.result_at=None;self.reset_at=None
    async def prepare(self):pass
    async def read(self,key):
        if key=="product":return 1
        assert key=="start"
        self.start_reads+=1
        return self.start_reads!=1
    async def put(self,key,value):
        now=time.monotonic();self.events.append((key,value,now))
        if key in ("finish","ng","error") and value:
            self.output_high=True;self.result_at=now
    async def reset(self):
        was_high=self.output_high
        for key in ("finish","running","ng","error"):await self.put(key,False)
        self.output_high=False
        if was_high:
            self.reset_at=time.monotonic();self.stop.set()
        self.state.update(outcome="대기",result_code=0,actual="-",detail="")
    async def finish(self,result,detail):await OpcIO.finish(self,result,detail)
    def logger(self,*args):pass


async def main():
    stop=threading.Event();io=FakeIO(stop)
    common={"max_frame_age":2,"timeout_seconds":2,"decision_confidence":.8,
            "stable_frames":1,"result_pulse_seconds":1.0,"vision3_lamp_on_delay_seconds":0}
    await asyncio.wait_for(station_cycle(io,Camera(),common,stop),3)
    assert io.reset_at-io.result_at>=.95
    running_off=next(i for i,e in enumerate(io.events) if e[0:2]==("running",False) and i>4)
    finish_on=next(i for i,e in enumerate(io.events) if e[0:2]==("finish",True))
    assert running_off<finish_on
    print("PASS: RUNNING OFF first, result pulse for one second, then automatic reset")


asyncio.run(main())
