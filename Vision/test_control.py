"""실물 장비 없이 판정, 태그 오류 진단, 원본 영상 독립성을 확인합니다."""
import asyncio
import json
from pathlib import Path
from engine import decide, order_parts, IO
cfg=json.loads(Path(__file__).with_name('config.json').read_text())
def det(names,score=.95):return [{'name':n,'score':score} for n in names]
for code in range(1,9):
 color,lamp,count=order_parts(code)
 for s in cfg['stations'][:3]:
  rev={v:k for k,v in s['classes'].items()}
  vals=[lamp] if s['id']==1 else [color] if s['id']==2 else [color]*count+[lamp]
  assert decide(s,det([rev[v] for v in vals]),code,.8)==('OK',0)
  assert decide(s,det([rev[v] for v in vals],.34),code,.8) is None
assert decide(cfg['stations'][2],det(['lamp_round_02']),1,.8)==('NG',0)
s4={'id':4,'classes':{f'cls_{i}':i for i in range(1,7)}}
for i in range(1,7):assert decide(s4,det([f'cls_{i}']),None,.8)==('OK',i)
assert decide(s4,[],None,.8)==('OK',1)
async def output_order():
 io=IO(None,{'id':4},True,lambda *args:None);events=[]
 async def put(k,v):events.append((k,v))
 io.put=put
 await io.finish(('OK',4),'test')
 assert events==[('result',4),('ng',False),('error',False),('running',False),('finish',True)]
 events.clear();await io.finish(('ERROR',0),'test')
 assert events[0]==('result',0) and ('error',True) in events and events[-1]==('finish',True)
asyncio.run(output_order())
async def binary_result_order():
 io=IO(None,{'id':1},True,lambda *args:None);events=[]
 async def put(k,v):events.append((k,v))
 io.put=put
 await io.finish(('OK',0),'same part')
 assert events==[('ng',False),('error',False),('running',False),('finish',True)]
 events.clear();await io.finish(('NG',0),'wrong part')
 assert events==[('ng',True),('error',False),('running',False),('finish',True)]
asyncio.run(binary_result_order())
print('PASS: V1 lamp/V2 seat 8 orders, NG 0=OK/1=NG, uncertain detection, V4 codes and FINISH-last')
