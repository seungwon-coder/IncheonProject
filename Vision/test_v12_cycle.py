import asyncio
import threading
import time
from engine import station_loop


class Camera:
    def __init__(self):
        self.seq=0
    def get(self):
        self.seq+=1
        detection={'name':'Gloss/Standard','score':.99}
        return (self.seq,time.monotonic(),None,[detection]),None


class IO:
    def __init__(self,stop):
        self.config={'id':2,'inspection':'seat','classes':{}}
        self.stop=stop;self.events=[];self.status='';self.finish_high=False
        self.completed=0;self.product_reads=0;self.start_reads=0
    async def prepare(self):
        pass
    async def read(self,key):
        if key=='product':
            self.product_reads+=1
            return 1  # PLC가 리셋 후에도 주문을 바꾸지 않아 기존 COCOA 주문을 유지
        if key=='start':
            self.start_reads+=1
            if self.start_reads==1:return False  # 프로그램 기동 당시 START OFF
            # FINISH를 읽은 PLC가 START를 OFF로 내려 각 검사 사이클을 리셋합니다.
            return not self.finish_high
        raise AssertionError(key)
    async def put(self,key,value):
        self.events.append((key,value))
        if key=='finish':self.finish_high=bool(value)
    async def reset(self):
        was_finished=self.finish_high
        for key in ('finish','running','ng','error'):
            await self.put(key,False)
        if was_finished:
            self.completed+=1
            if self.completed==1:self.stop.set()
    async def finish(self,result,reason):
        outcome,_=result
        await self.put('ng',outcome=='NG')
        await self.put('error',outcome=='ERROR')
        await self.put('running',False)
        await self.put('finish',True)
    def logger(self,*args):
        pass


async def run():
    stop=threading.Event();io=IO(stop)
    common={'max_frame_age':2,'timeout_seconds':2,'decision_confidence':.8,'stable_frames':1,'result_pulse_seconds':.1}
    await asyncio.wait_for(station_loop(io,Camera(),common,stop),3)
    assert io.completed==1 and io.product_reads>=1
    assert io.events.count(('finish',True))==1
    assert io.events.count(('ng',False))>=1
    assert ('ng',True) not in io.events
    print('PASS: V2 START cycle outputs FINISH pulse and resets automatically')


asyncio.run(run())
