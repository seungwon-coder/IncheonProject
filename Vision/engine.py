"""PV5 4-camera OPC-UA integration prototype. Korean setup: README_KO.md.
Default preview/dry-run. --write enables actual PLC output writes.
"""
import asyncio
import argparse
import json
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def order_parts(code):
    if type(code) is not int or not 1 <= code <= 8:
        raise ValueError('주문은 1~8이어야 합니다. 0=생산 없음')
    return ('cocoa' if code in (1,2,5,6) else 'dark',
            'round' if code % 2 else 'edge', 2 if code <= 4 else 1)


# 사용자가 확인한 클래스만 연결합니다. 번호와 무관하게 이름으로 판정합니다.
DEFAULT_V4 = {'car body':6, 'car lamp_A':3, 'car lamp_R':2,
              'Gloss/Standard':4, 'Matte Brown':5, 'car seat':'seat_unknown'}

ALIASES = {'lamp_edge_01':'edge', 'lamp_round_02':'round',
           'lamp_edge':'edge', 'lamp_round':'round',
           'seat_color_01':'cocoa', 'seat_color_02':'dark',
           'seat_cocoa':'cocoa', 'seat_dark_brown':'dark',
           'car lamp_A':'edge', 'car lamp_R':'round',
           'Matte Brown':'dark', 'Gloss/Standard':'cocoa',
           'car body':'body', 'car seat':'seat_unknown'}

def class_mapping(station):
    if station['id']==4:
        return {**DEFAULT_V4, **station.get('classes',{})}
    return {**ALIASES, **station['classes']}


def inspection_role(station):
    """검사 역할은 카메라 번호와 분리하며 v12 기본값은 V1=램프, V2=시트입니다."""
    defaults={1:'lamp',2:'seat',3:'assembly',4:'sort'}
    role=station.get('inspection',defaults.get(station['id']))
    if role not in ('seat','lamp','assembly','sort'):
        raise ValueError(f"지원하지 않는 검사 역할: {role}")
    return role


def relevant_values(station):
    return {
        'seat':{'cocoa','dark','seat_unknown'},
        'lamp':{'edge','round'},
        'assembly':{'edge','round','cocoa','dark','seat_unknown'},
        'sort':{2,3,4,5,6,'seat_unknown'},
    }[inspection_role(station)]

def validate_model(station, task, names):
    names=set(names)
    mapping=class_mapping(station)
    if task!='detect':
        raise ValueError('객체 검출 모델이 필요합니다. 분류 모델은 별도 연결이 필요합니다.')
    unknown=names-set(mapping)
    if unknown:
        raise ValueError(f'미등록 클래스: {sorted(unknown)}; 태그 / 클래스 설정에서 확인하세요')
    if inspection_role(station)=='sort':
        if not ({mapping[n] for n in names} & {2,3,4,5,6,'seat_unknown'}):
            raise ValueError('비전4에서 사용할 수 있는 클래스가 없습니다')
        return
    required=relevant_values(station)
    if not ({mapping[n] for n in names}&required):
        raise ValueError('이 비전 검사에 필요한 클래스가 모델에 없습니다')


def decide(station, detections, order, threshold):
    # 비전별 검사 대상만 판정합니다. 차체는 화면 표시만 하며 베이스로 추정하지 않습니다.
    n=station['id'];role=inspection_role(station)
    mapping=class_mapping(station)
    # 비전4 코드1은 빈 지그입니다. 연속된 새 추론 프레임의 비검출로 확정합니다.
    if role=='sort' and not detections:
        return ('OK',1)
    if any(d['name'] not in mapping for d in detections):return None
    if role!='sort':
        relevant=relevant_values(station)
        detections=[d for d in detections if mapping[d['name']] in relevant]
        if any(mapping[d['name']]=='seat_unknown' for d in detections):return None
    # 검출 없음은 빈지그가 아닙니다. 불확실한 검출은 ERROR로 종료할 때까지 대기합니다.
    if not detections or any(d['score'] < threshold for d in detections):
        return None
    mapping = class_mapping(station)
    if any(d['name'] not in mapping for d in detections):
        return None
    values = [mapping[d['name']] for d in detections]
    n = station['id']
    if role == 'sort':
        if len(values) != 1 or type(values[0]) is not int or values[0] not in range(1,7):
            return None
        return ('OK', values[0])
    color, lamp, count = order_parts(order)
    if role in ('seat','lamp'):
        if len(values) != 1:
            return None
        expected=color if role=='seat' else lamp
        return ('OK' if values[0] == expected else 'NG', 0)
    c = Counter(values)
    if c['edge'] + c['round'] > 1 or c['cocoa'] + c['dark'] > 2:
        return None
    return ('OK' if c == Counter({color: count, lamp: 1}) else 'NG', 0)


class Camera:
    def __init__(self, config, stop):
        self.config = config; self.stop = stop
        self.lock = threading.Lock(); self.packet = None; self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def get(self):
        with self.lock:
            return self.packet, self.error

    def run(self):
        import cv2
        from ultralytics import YOLO
        cap = None
        try:
            s = self.config
            path = ROOT / s['model']
            if not path.is_file(): raise ValueError(f'모델 파일 없음: {path}')
            model = YOLO(str(path))
            names = set(model.names.values())
            validate_model(s, model.task, names)
            from camera_identity import resolve_camera
            camera_index, camera_backend, identity = resolve_camera(s)
            cap = cv2.VideoCapture(camera_index, camera_backend)
            if not cap.isOpened(): raise RuntimeError('카메라 열기 실패')
            for prop,value in [(cv2.CAP_PROP_FRAME_WIDTH,640),(cv2.CAP_PROP_FRAME_HEIGHT,480),
                (cv2.CAP_PROP_AUTOFOCUS,0),(cv2.CAP_PROP_FOCUS,s['focus']),
                (cv2.CAP_PROP_AUTO_EXPOSURE,.25),(cv2.CAP_PROP_EXPOSURE,s['exposure'])]:
                cap.set(prop,value)
            print(f"V{s['id']} camera={camera_index} name={getattr(identity, 'name', '')} focus={cap.get(cv2.CAP_PROP_FOCUS)} exposure={cap.get(cv2.CAP_PROP_EXPOSURE)}")
            seq = 0
            while not self.stop.is_set():
                ok, frame = cap.read()
                if not ok: raise RuntimeError('카메라 읽기 실패')
                captured = time.monotonic()
                r = model.predict(frame, conf=.25, imgsz=640, device='cpu', verbose=False)[0]
                detections = [{'name':model.names[int(c)],'score':float(score),'box':box}
                              for c,score,box in zip(r.boxes.cls.cpu().tolist(),r.boxes.conf.cpu().tolist(),r.boxes.xyxy.cpu().tolist())]
                seq += 1
                with self.lock: self.packet = (seq, captured, frame, detections)
        except Exception as e:
            with self.lock: self.error = f'{type(e).__name__}: {e}'
        finally:
            if cap is not None: cap.release()


class IO:
    def __init__(self, client, config, write, logger):
        self.client=client; self.config=config; self.write=write; self.logger=logger
        self.nodes={}; self.types={}; self.status='START OFF 대기'

    async def prepare(self):
        from asyncua import ua
        tags = self.config['tags']
        required = ['start','running','finish','ng','error'] + (['result'] if self.config['id']==4 else ['product'])
        for key in required:
            if not tags.get(key): raise ValueError(f'{key}의 실제 NodeId 미설정')
            try:
                node=self.client.get_node(tags[key]); self.nodes[key]=node
                typ=await node.read_data_type_as_variant_type(); self.types[key]=typ
                if key not in ('product','result') and typ != ua.VariantType.Boolean:
                    raise ValueError(f'{key}: Boolean이 아님')
                if key in ('product','result') and typ not in (ua.VariantType.Int16,ua.VariantType.UInt16,ua.VariantType.Int32,ua.VariantType.UInt32):
                    raise ValueError(f'{key}: 지원하는 정수 자료형이 아님')
                await self.read(key)
                if key not in ('start','product'):
                    access=await node.get_user_access_level()
                    if self.write and ua.AccessLevel.CurrentWrite not in access:
                        raise ValueError(f'{key}: 쓰기 권한 없음')
            except Exception as e:
                raise RuntimeError(f"{key} / {tags[key]} / {e}") from e

    async def read(self,key):
        d=await self.nodes[key].read_data_value(raise_on_bad_status=False)
        if not d.StatusCode.is_good(): raise RuntimeError(f'{key} 품질 {d.StatusCode.name}')
        return d.Value.Value

    async def put(self,key,value):
        from asyncua import ua
        if self.write:
            # Value만 씁니다. OPC-UA 서버 응답 성공은 물리 출력 확인과 구분합니다.
            await self.nodes[key].write_value(ua.DataValue(ua.Variant(value,self.types[key])))
        self.logger(self.config['id'], 'write' if self.write else 'dry_write', {key:value})

    async def reset(self):
        # 완료를 먼저 내리고 나머지를 초기화. START/주문은 절대 쓰지 않습니다.
        for key in ('finish','running','ng','error'): await self.put(key,False)
        if self.config['id']==4: await self.put('result',0)

    async def finish(self,result,reason):
        outcome,code=result
        if self.config['id']==4: await self.put('result',code if outcome=='OK' else 0)
        await self.put('ng',outcome=='NG')
        await self.put('error',outcome=='ERROR')
        await self.put('running',False)
        # FINISH를 마지막에 올립니다. PLC는 FINISH 이후 결과를 읽어야 합니다.
        await self.put('finish',True)
        self.status=f'{outcome}: {reason}'
        self.logger(self.config['id'],'complete',{'outcome':outcome,'code':code,'reason':reason})


async def station_loop(io, camera, common, stop):
    try:
        await io.prepare()
        ready_deadline=time.monotonic()+60
        while not stop.is_set():
            packet,error=camera.get()
            if error: raise RuntimeError(error)
            if packet and time.monotonic()-packet[1]<=common['max_frame_age']: break
            if time.monotonic()>ready_deadline: raise RuntimeError('카메라/모델 준비 시간 초과')
            io.status='WAIT: camera/model loading'
            await asyncio.sleep(.1)
        # 기동 당시 START=ON인 경우 과거 검사를 재실행하지 않습니다.
        while not stop.is_set():
            if not await io.read('start'): break
            io.status='WAIT: START를 OFF로 내려주세요'
            await asyncio.sleep(.1)
        if stop.is_set(): return
        await io.reset()
        while not stop.is_set():
            io.status='READY: START ON 대기'
            while not stop.is_set() and not await io.read('start'): await asyncio.sleep(.1)
            if stop.is_set(): return
            started=time.monotonic();order=None
            await io.reset(); await io.put('running',True)
            io.status='RUNNING'
            result=None;reason='안정된 검출 없음/시간 초과';signature=None;streak=0;last_seq=-1
            if io.config['id']!=4:
                order=await io.read('product')
                try: order_parts(order)
                except ValueError as e: result=('ERROR',0);reason=str(e)
            while not stop.is_set() and result is None and time.monotonic()-started<common['timeout_seconds']:
                if not await io.read('start'):
                    result=('ERROR',0);reason='검사 중 START 조기 OFF';break
                if io.config['id']!=4 and await io.read('product')!=order:
                    result=('ERROR',0);reason='검사 중 주문 변경';break
                packet,error=camera.get()
                if not packet or time.monotonic()-packet[1]>common['max_frame_age']:
                    streak=0;signature=None
                if error: result=('ERROR',0);reason=error;break
                if packet:
                    seq,captured,_,det=packet
                    if captured>=started and time.monotonic()-captured<=common['max_frame_age'] and seq!=last_seq:
                        last_seq=seq
                        candidate=decide(io.config,det,order,common['decision_confidence'])
                        sig=(candidate,tuple(sorted(d['name'] for d in det))) if candidate else None
                        streak=streak+1 if sig and sig==signature else (1 if sig else 0)
                        signature=sig
                        if streak>=common['stable_frames']:
                            result=candidate;reason='연속 프레임 부품 비교';break
                await asyncio.sleep(.05)
            if stop.is_set(): return
            if result is None: result=('ERROR',0)
            # 완료 직전에도 주문과 START를 재확인합니다.
            if not await io.read('start'):
                await io.reset();io.logger(io.config['id'],'aborted',{'reason':'START 조기 OFF'});continue
            if io.config['id']!=4 and await io.read('product')!=order:
                result=('ERROR',0);reason='완료 직전 주문 변경'
            await io.finish(result,reason)
            # 결과를 유지하여 PLC가 읽을 시간을 보장합니다. START OFF가 확인 응답입니다.
            while not stop.is_set() and await io.read('start'): await asyncio.sleep(.1)
            if not stop.is_set(): await io.reset()
    except Exception as e:
        io.status=f'BLOCKED: {e}'
        io.logger(io.config['id'],'blocked',{'error':str(e)})
        # 통신 장애 중 불확실한 쓰기를 자동 재시도하지 않습니다.
        print(f"V{io.config['id']} 연동 중단: {e}. PLC에서 출력 상태를 확인하고 프로그램을 다시 시작하세요.")


async def main(args):
    import cv2
    import numpy as np
    from asyncua import Client
    cfg=json.loads((ROOT/args.config).read_text(encoding='utf-8-sig'))
    stations=[s for s in cfg['stations'] if s['enabled']]
    if len({s['camera'] for s in stations})!=len(stations): raise ValueError('카메라 번호 중복')
    ids=[s['id'] for s in stations]
    if len(set(ids))!=len(ids) or any(i not in (1,2,3,4) for i in ids): raise ValueError('비전 ID 오류')
    if not stations: raise ValueError('활성화한 비전 없음')
    output_nodes=[v for s in stations for k,v in s['tags'].items() if k not in ('start','product') and v]
    input_nodes=[v for s in stations for k,v in s['tags'].items() if k in ('start','product') and v]
    if len(output_nodes)!=len(set(output_nodes)) or set(output_nodes)&set(input_nodes): raise ValueError('입출력 NodeId 중복')
    folder=ROOT/'logs';folder.mkdir(exist_ok=True)
    filename=folder/(datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.jsonl')
    def logger(s,event,data):
        with filename.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'time':datetime.now().isoformat(),'station':s,'event':event,**data},ensure_ascii=False)+'\n')
    stop=threading.Event();cameras=[];tasks=[];ios=[]
    try:
        async with Client(cfg['endpoint'],timeout=3) as client:
            print('OPC-UA 연결 성공. 모드:', '실제 PLC 쓰기' if args.write else 'DRY RUN (PLC 쓰기 없음)')
            for s in stations:
                cam=Camera(s,stop);cameras.append(cam)
                io=IO(client,s,args.write,logger);ios.append(io)
                if s['id']==4 and (not s['tags'].get('result') or not {2,3,4,5,6}.issubset(set(class_mapping(s).values()))):
                    io.status='BLOCKED: V4 result NodeId / class mapping 2~6 required';print(io.status);continue
                cam.thread.start()
                tasks.append(asyncio.create_task(station_loop(io,cam,cfg,stop)))
            while not stop.is_set():
                for cam,io in zip(cameras,ios):
                    packet,error=cam.get()
                    frame=np.zeros((480,640,3),dtype=np.uint8) if not packet else packet[2].copy()
                    age=0 if not packet else time.monotonic()-packet[1]
                    if packet and age<=cfg['max_frame_age']:
                        for d in packet[3]:
                            x1,y1,x2,y2=map(int,d['box']);color=(0,180,255) if d['score']<cfg['decision_confidence'] else (0,255,0)
                            cv2.rectangle(frame,(x1,y1),(x2,y2),color,2)
                            cv2.putText(frame,f"{d['name']} {d['score']:.2f}",(x1,max(15,y1)),cv2.FONT_HERSHEY_SIMPLEX,.45,color,1)
                    if not packet or age>cfg['max_frame_age'] or error:
                        cv2.putText(frame,'NO FRESH FRAME / CHECK CONSOLE',(10,60),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,0,255),2)
                    # 영문 상태를 영상에, 상세 한국어는 터미널/로그에 표시합니다.
                    text=io.status.split(':')[0].split(' ')[0]
                    cv2.putText(frame,f"V{io.config['id']} {text} | {'WRITE' if args.write else 'DRY'} | Q:QUIT",(10,25),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),2)
                    cv2.imshow(f"PV5 V{io.config['id']}",frame)
                if cv2.waitKey(1)&0xff in (ord('q'),27): stop.set()
                await asyncio.sleep(.03)
    finally:
        stop.set()
        for t in tasks:t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        cv2.destroyAllWindows()
        print('종료: PLC 출력은 자동 초기화하지 않습니다. PLC의 통신 타임아웃/초기화 절차로 복귀하세요.')
        print('로그:',filename)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='config.json')
    parser.add_argument('--write',action='store_true',help='실제 PLC 쓰기 활성화')
    args=parser.parse_args()
    try: asyncio.run(main(args))
    except KeyboardInterrupt: print('사용자 종료. PLC 상태를 확인하세요.')
    except Exception as e: print(f'실행 오류: {type(e).__name__}: {e}');raise SystemExit(1)
