import asyncio
import threading
import time
from engine import station_loop

class Camera:
    def __init__(self):self.seq=0
    def get(self):
        self.seq+=1
        return (self.seq,time.monotonic(),None,[{'name':'car lamp_R','score':.99}]),None

class IO:
    def __init__(self,stop):
        self.config={'id':4,'classes':{}};self.stop=stop;self.events=[];self.reads=0;self.status=''
    async def prepare(self):pass
    async def read(self,key):
        assert key=='start';self.reads+=1
        # 연결 직후 OFF, 새 사이클 ON 유지, PLC 결과 확인 후 OFF
        return self.reads not in (1,) and self.reads<6
    async def put(self,key,value):
        self.events.append((key,value))
        if key=='result' and value==0 and ('result',2) in self.events:self.stop.set()
    async def reset(self):
        for key in ('finish','running','ng','error'):await self.put(key,False)
        await self.put('result',0)
    async def finish(self,result,reason):
        outcome,code=result
        await self.put('result',code if outcome=='OK' else 0)
        await self.put('ng',outcome=='NG');await self.put('error',outcome=='ERROR')
        await self.put('running',False);await self.put('finish',True)
    def logger(self,*args):pass

async def run():
    stop=threading.Event();io=IO(stop)
    common={'max_frame_age':2,'timeout_seconds':2,'decision_confidence':.8,'stable_frames':2}
    await asyncio.wait_for(station_loop(io,Camera(),common,stop),3)
    result_at=io.events.index(('result',2));finish_at=io.events.index(('finish',True))
    final_zero=max(i for i,e in enumerate(io.events) if e==('result',0))
    assert result_at<finish_at<final_zero
    print('PASS: V4 START tag -> CLASS_RESULT2 -> FINISH -> START OFF -> result/status reset')

asyncio.run(run())
