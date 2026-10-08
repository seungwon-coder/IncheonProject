"""실물 장비 없이 판정, 태그 오류 진단, 원본 영상 독립성을 확인합니다."""
import asyncio
import json
from pathlib import Path
from engine import decide, order_parts, IO
from vision_core_server import Detection, vision3_seat_counts
cfg=json.loads(Path(__file__).with_name('config.json').read_text())
v3=next(s for s in cfg['stations'] if s['id']==3)
assert v3['decision_confidence']==0.60 and v3['nms_iou']==0.55
def det(names,score=.95):return [{'name':n,'score':score} for n in names]
for code in range(1,9):
 color,lamp,count=order_parts(code)
 for s in cfg['stations'][:3]:
  rev={v:k for k,v in s['classes'].items()}
  vals=[lamp] if s['id']==1 else [color] if s['id']==2 else ['body']+[color]*count+['lamp_on']
  assert decide(s,det([rev[v] for v in vals]),code,.8)==('OK',0)
  assert decide(s,det([rev[v] for v in vals],.34),code,.8) is None
assert decide(cfg['stations'][2],det(['car body','seat_color_01','seat_color_01']),1,.8)==('NG',0)
# 비전3은 차체 없이도 주문별 시트 수량과 LED ON만 맞으면 OK입니다.
assert decide(cfg['stations'][2],det(['seat_color_01','seat_color_01','lamp_on']),1,.8)==('OK',0)
assert decide(cfg['stations'][2],det(['car body','seat_color_01','seat_color_01','lamp_on']),1,.8)==('OK',0)
assert decide(cfg['stations'][2],det(['seat_color_01','lamp_on']),1,.8)==('NG',0)
assert decide(cfg['stations'][2],det(['seat_color_01','seat_color_01']),1,.8)==('NG',0)
raw_seats=[Detection('car seat','seat_unknown',.99,[0,0,10,10],None),
           Detection('car seat','seat_unknown',.787,[20,0,30,10],None)]
assert vision3_seat_counts(raw_seats,raw_seats,.60)==(2,2)
assert vision3_seat_counts(raw_seats,raw_seats,.80)==(2,1)
s4={'id':4,'classes':{f'cls_{value}':value for value in ('round','edge','cocoa','dark','body')}}
for value,code in (('round',2),('edge',3),('cocoa',4),('dark',5),('body',6)):
 assert decide(s4,det([f'cls_{value}']),None,.8)==('OK',code)
assert decide(s4,[],None,.8) is None
async def output_order():
 io=IO(None,{'id':4},True,lambda *args:None);events=[]
 async def put(k,v):events.append((k,v))
 io.put=put
 await io.finish(('OK',4),'test')
 assert events==[('running',False),('finish',False),('ng',False),('error',False),('result',4),('finish',True)]
 events.clear();await io.finish(('ERROR',0),'test')
 assert events==[('running',False),('finish',False),('ng',False),('error',False),('result',0),('error',True)]
asyncio.run(output_order())
async def binary_result_order():
 io=IO(None,{'id':1},True,lambda *args:None);events=[]
 async def put(k,v):events.append((k,v))
 io.put=put
 await io.finish(('OK',0),'same part')
 assert events==[('running',False),('finish',False),('ng',False),('error',False),('finish',True)]
 events.clear();await io.finish(('NG',0),'wrong part')
 assert events==[('running',False),('finish',False),('ng',False),('error',False),('ng',True)]
 events.clear();await io.finish(('ERROR',0),'missing')
 assert events==[('running',False),('finish',False),('ng',False),('error',False),('error',True)]
asyncio.run(binary_result_order())
print('PASS: V1-V3 exclusive FINISH/NG/ERROR and V4 part+FINISH or ERROR')
