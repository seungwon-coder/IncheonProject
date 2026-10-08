"""Local learning MES: live MySQL telemetry + explicitly manual execution records."""
import ctypes
import datetime as dt
import json
import logging
from logging.handlers import RotatingFileHandler
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import urlparse
import uuid

BASE = Path(__file__).resolve().parent
MANUFACTURING = BASE.parent
CONFIG = json.loads((MANUFACTURING / 'config.json').read_text(encoding='utf-8'))
MYSQL = CONFIG['mysql_exe']
OPTIONS = MANUFACTURING / 'mysql-client.ini'
DATABASE = '`opcua-manufacturing`'
MUTATION_LOCK = threading.RLock()
LIVE_LOCK = threading.Lock()
LIVE = {'connected': False, 'error': '', 'tags': {}, 'checked_at': None}
STAGES = [
 ('SUPPLY','부품 공급'),('VISION1','램프 조립전 검사'),('ROBOT1','램프 조립'),
 ('VISION2','시트 조립전 검사'),('ROBOT2','시트 조립'),('ROBOT3','바디 조립'),
 ('VISION3','최종 조립검사'),('SERVO','완료창고 적재')
]


def now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')


def uid():
    return str(uuid.uuid4())


def mode():
    value=json.loads((BASE/'mode.json').read_text(encoding='utf-8'))['mode']
    if value not in ('TEST','PRODUCTION'): raise ValueError('운영 모드가 유효하지 않습니다.')
    return value


def set_mode(payload):
    person=operator(payload);value=payload.get('mode')
    if value not in ('TEST','PRODUCTION'): raise ValueError('운영 모드를 확인하세요.')
    target=BASE/'mode.json';temp=BASE/'mode.tmp'
    temp.write_text(json.dumps({'mode':value,'changed_by':person,'changed_at':now()},ensure_ascii=False),encoding='utf-8')
    temp.replace(target)
    return {'message':('공정테스트' if value=='TEST' else '생산')+' 저장 모드로 전환됐습니다. PLC 제어 상태는 변경되지 않습니다.'}


def sql(value):
    if value is None:
        return 'NULL'
    if isinstance(value, (int, float)):
        return str(value)
    return "CONVERT(X'" + str(value).encode('utf-8').hex() + "' USING utf8mb4) COLLATE utf8mb4_unicode_ci"


def execute(statement):
    result = subprocess.run([MYSQL, f'--defaults-file={OPTIONS}', '--batch', '--skip-column-names', '--connect-timeout=4'],
                            input=f'USE {DATABASE};\n' + statement, encoding='utf-8', capture_output=True,
                            timeout=20, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def rows(table, fields, where='1=1', order=''):
    pairs = ','.join(f"'{field}',`{field}`" for field in fields)
    result = execute(f"SELECT HEX(JSON_OBJECT({pairs})) FROM `{table}` WHERE {where} {order};")
    return [json.loads(bytes.fromhex(line.strip()).decode('utf-8')) for line in result.splitlines() if line.strip()]


def one(table, fields, key, value):
    result = rows(table,fields,f'`{key}`={sql(value)}')
    if not result:
        raise ValueError('대상 기록을 찾을 수 없습니다.')
    return result[0]


ORDER_FIELDS=['order_id','model','lamp','seat','quantity','program_no','created_at','operator_name','run_mode']
UNIT_FIELDS=['unit_id','order_id','stage','state','return_location','updated_at','run_mode']
EVENT_FIELDS=['event_id','unit_id','stage_code','result','operator_name','note','occurred_at','source','run_mode']
APPROVAL_FIELDS=['approval_id','unit_id','state','requested_at','decided_at','approver','reason','run_mode']


def operator(payload):
    name=str(payload.get('operator','')).strip()
    if not name or len(name)>100:
        raise ValueError('작업자 이름을 입력하세요.')
    return name


def event_sql(unit, stage, result, person, note=''):
    values=[uid(),unit,stage,result,person,note,now(),'MANUAL',mode()]
    return 'INSERT INTO mes_events ('+','.join(EVENT_FIELDS)+') VALUES ('+','.join(sql(v) for v in values)+');\n'


def inventory_changes(changes):
    inventory={item['item_id']:item for item in rows('mes_inventory',['item_id','quantity','capacity'],f'run_mode={sql(mode())}')}
    statements=[]
    for item,delta in changes.items():
        current=inventory[item]
        quantity=current['quantity']+delta
        if quantity<0:
            raise ValueError(f'{item}: 재고가 부족합니다. 재고 확인 후 보충하세요.')
        if quantity>current['capacity']:
            raise ValueError(f'{item}: 창고 최대 수량 {current["capacity"]}을 초과합니다.')
        statements.append(f"UPDATE mes_inventory SET quantity={quantity},updated_at={sql(now())},source='MANUAL' WHERE item_id={sql(item)} AND run_mode={sql(mode())};")
    return '\n'.join(statements)+'\n'


QUEUE_FIELDS=['unit_id','position','state','kind','parent_unit_id','created_at','run_mode']

def queue_rows():
    return rows('mes_dispatch_queue',QUEUE_FIELDS,f"run_mode={sql(mode())} AND state='waiting'",'ORDER BY position,created_at,unit_id')

def queue_append(unit):
    queue=queue_rows()
    position=max((q['position'] for q in queue),default=0)+10
    return f"INSERT INTO mes_dispatch_queue VALUES ({sql(unit)},{position},'waiting','NORMAL',NULL,{sql(now())},{sql(mode())});"

def retry_sql(unit,person):
    attempts=rows('mes_units',['unit_id'],f"order_id={sql(unit['order_id'])}")
    root=unit['unit_id'].split('-R')[0]
    number=1
    while any(u['unit_id']==root+'-R'+str(number) for u in attempts): number+=1
    replacement=root+'-R'+str(number)
    queue=[q for q in queue_rows() if q['unit_id']!=unit['unit_id']]
    # Keep the next scheduled unit ahead; place this remake directly after it.
    ordered=[q['unit_id'] for q in queue]
    active=rows('mes_dispatch_queue',['unit_id'],f"run_mode={sql(mode())} AND state='started' AND unit_id<>{sql(unit['unit_id'])}")
    ordered.insert(0 if active else (1 if ordered else 0),replacement)
    statement=f"INSERT INTO mes_units VALUES ({sql(replacement)},{sql(unit['order_id'])},0,'planned',NULL,{sql(now())},{sql(mode())});"
    statement+=f"UPDATE mes_dispatch_queue SET state='failed' WHERE unit_id={sql(unit['unit_id'])};"
    statement+=f"INSERT INTO mes_dispatch_queue VALUES ({sql(replacement)},0,'waiting','REMAKE',{sql(unit['unit_id'])},{sql(now())},{sql(mode())});"
    for index,identifier in enumerate(ordered,1):
        statement+=f"UPDATE mes_dispatch_queue SET position={index*10} WHERE unit_id={sql(identifier)};"
    statement+=event_sql(unit['unit_id'],'REPRODUCTION','QUEUED',person,'최종 NG · 재생산 제품 '+replacement+' · 다음 예정 제품 뒤에 배치')
    payload={'failed_unit':unit['unit_id'],'replacement_unit':replacement,'order_id':unit['order_id'],'queue':ordered,'policy':'AFTER_NEXT'}
    statement+=f"INSERT INTO mes_scada_outbox VALUES ({sql(uid())},'REMAKE_QUEUED',{sql(json.dumps(payload,ensure_ascii=False))},'WAITING_CONFIG',{sql(now())},{sql(mode())});"
    return statement


def create_order(payload):
    person=operator(payload)
    model,lamp,seat=payload.get('model'),payload.get('lamp'),payload.get('seat')
    if model not in ('VAN','TRUCK') or lamp not in ('ROUND','ANGULAR') or seat not in ('COCOA','DARK'):
        raise ValueError('제품 구성 항목을 확인하세요.')
    option=model+'-'+('B' if seat=='DARK' else 'C')+'-'+('A' if lamp=='ROUND' else 'B')
    order_id=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime('%Y%m%d-%H%M%S%f')+'-'+option
    quantity=int(payload.get('quantity',1))
    if not 1<=quantity<=100:
        raise ValueError('주문 수량은 1~100입니다.')
    program=payload.get('program_no')
    program=None if program in ('',None) else int(program)
    if program is not None and not 0<=program<=65535:
        raise ValueError('PLC 프로그램 번호 범위를 확인하세요.')
    if rows('mes_orders',['order_id'],f'order_id={sql(order_id)}'):
        raise ValueError('이미 등록된 주문번호입니다.')
    values=[order_id,model,lamp,seat,quantity,program,now(),person,mode()]
    statements='INSERT INTO mes_orders ('+','.join(ORDER_FIELDS)+') VALUES ('+','.join(sql(v) for v in values)+');\n'
    for index in range(1,quantity+1):
        unit=f'{order_id}-{index:03d}'
        stage=int(payload.get('initial_stage',0)) if payload.get('adopt') else 0
        if not 0<=stage<=6: raise ValueError('기존 생산품은 공급부터 최종검사 대기까지의 단계로 등록하세요.')
        unit_state='running' if stage>0 else 'planned'
        statements+=f"INSERT INTO mes_units VALUES ({sql(unit)},{sql(order_id)},{stage},{sql(unit_state)},NULL,{sql(now())},{sql(mode())});\n"
        if not payload.get('adopt'):
            statements+=queue_append(unit)+f"UPDATE mes_dispatch_queue SET position=position+{index*10} WHERE unit_id={sql(unit)};"
        if payload.get('adopt'):
            statements+=event_sql(unit,'ADOPTED','CONFIRMED',person,'이미 생산 중인 제품 등록 · 이전 공정·재고 차감은 추정하지 않음')
    execute('START TRANSACTION;\n'+statements+'COMMIT;')
    return {'message':'MES 주문번호 생성: '+order_id,'order_id':order_id}


def advance(payload):
    person=operator(payload)
    unit=one('mes_units',UNIT_FIELDS,'unit_id',payload['unit_id'])
    if unit['run_mode']!=mode(): raise ValueError('제품의 저장 모드로 전환한 뒤 진행하세요.')
    if unit['state'] not in ('planned','running'):
        raise ValueError('이 제품은 현재 다음 공정을 진행할 수 없습니다.')
    stage=unit['stage']
    if not 0<=stage<8:
        raise ValueError('공정 상태가 유효하지 않습니다.')
    code,label=STAGES[stage]
    result=payload.get('result','OK')
    if result not in ('OK','NG') or (result=='NG' and code not in ('VISION1','VISION2','VISION3')):
        raise ValueError('검사 단계에서만 NG를 등록할 수 있습니다.')
    note=str(payload.get('note','')).strip()
    if len(note)>1000:
        raise ValueError('메모는 1000자 이내입니다.')
    if result=='NG' and not note:
        raise ValueError('NG 원인을 입력하세요.')
    statements=''
    if stage==0:
        queue=queue_rows()
        entry=next((q for q in queue if q['unit_id']==unit['unit_id']),None)
        if entry and queue[0]['unit_id']!=unit['unit_id']:
            raise ValueError('순서 대기: '+queue[0]['unit_id']+' 먼저 공급 확인하세요.')
        if entry:
            statements+=f"UPDATE mes_dispatch_queue SET state='started' WHERE unit_id={sql(unit['unit_id'])};"
        order=one('mes_orders',ORDER_FIELDS,'order_id',unit['order_id'])
        statements+=inventory_changes({'BODY':-1,'CHASSIS':-1,'LAMP_'+order['lamp']:-1,'SEAT_'+order['seat']:-2 if order['model']=='VAN' else -1})
    new_stage,new_state=stage+1,'running'
    order=one('mes_orders',ORDER_FIELDS,'order_id',unit['order_id'])
    seat_done=len(rows('mes_events',['event_id'],f"unit_id={sql(unit['unit_id'])} AND stage_code='ROBOT2' AND result='OK'"))
    if stage in (3,4):
        note=f'시트 {seat_done+1}/{2 if order["model"]=="VAN" else 1} · '+note
    if result=='NG':
        new_stage,new_state=stage,('failed_final' if stage==6 else 'resupply_pending')
        if stage in (1,3):
            statements+=event_sql(unit['unit_id'],'RETURN_CONVEYOR','PENDING',person,('램프' if stage==1 else '시트')+' NG 부품 1개 리턴 확인 대기')
        else:
            statements+=retry_sql(unit,person)
    elif stage==4 and order['model']=='VAN' and seat_done==0:
        new_stage,new_state=3,'running'
    elif stage==6:
        new_stage,new_state=7,'awaiting_approval'
        approval_id=uid()
        statements+=f"INSERT INTO mes_approvals (approval_id,unit_id,state,requested_at,run_mode) VALUES ({sql(approval_id)},{sql(unit['unit_id'])},'waiting',{sql(now())},{sql(mode())});\n"
    elif stage==7:
        approved=rows('mes_approvals',['approval_id'],f"unit_id={sql(unit['unit_id'])} AND state='approved'")
        if not approved:
            raise ValueError('최종검사 승인이 먼저 필요합니다.')
        statements+=inventory_changes({'FINISHED':1})
        statements+=f"UPDATE mes_dispatch_queue SET state='completed' WHERE unit_id={sql(unit['unit_id'])};"
        new_state='completed'
    statements+=event_sql(unit['unit_id'],code,result,person,note)
    statements+=f"UPDATE mes_units SET stage={new_stage},state={sql(new_state)},updated_at={sql(now())} WHERE unit_id={sql(unit['unit_id'])};"
    execute('START TRANSACTION;\n'+statements+'\nCOMMIT;')
    if stage==6 and result=='OK':
        decide({'approval_id':approval_id,'decision':'approved','role':'admin','operator':'관리자 기본승인','reason':'최종검사 OK 확인 후 기본 자동 승인'})
        return {'message':'최종검사 OK · 관리자 기본승인 · PDF 자동 발행 완료'}
    return {'message':('최종검사 NG · 다음 예정 제품 이후 우선 재생산 큐 등록' if stage==6 else 'NG 부품 리턴·동일 부품 1개 재공급 확인 대기') if result=='NG' else label+' 완료 확인'}


def resupply(payload):
    person=operator(payload)
    unit=one('mes_units',UNIT_FIELDS,'unit_id',payload['unit_id'])
    if unit['run_mode']!=mode() or unit['state']!='resupply_pending' or unit['stage'] not in (1,3):
        raise ValueError('재공급 대기 제품인지 확인하세요.')
    order=one('mes_orders',ORDER_FIELDS,'order_id',unit['order_id'])
    item=('LAMP_'+order['lamp']) if unit['stage']==1 else ('SEAT_'+order['seat'])
    statement=inventory_changes({item:-1})
    statement+=event_sql(unit['unit_id'],'RETURN_CONVEYOR','CONFIRMED',person,'NG 부품 1개 리턴 확인')
    statement+=event_sql(unit['unit_id'],'RESUPPLY','CONFIRMED',person,item+' 동일 부품 1개 재공급 확인 · '+str(payload.get('note',''))[:500])
    statement+=f"UPDATE mes_units SET state='running',updated_at={sql(now())} WHERE unit_id={sql(unit['unit_id'])};"
    execute('START TRANSACTION;'+statement+'COMMIT;')
    return {'message':'NG 부품 1개 리턴·동일 부품 1개 재공급 기록 완료 · 같은 검사 재시도'}


def return_action(payload):
    person=operator(payload)
    unit=one('mes_units',UNIT_FIELDS,'unit_id',payload['unit_id'])
    if unit['run_mode']!=mode(): raise ValueError('제품의 저장 모드로 전환하세요.')
    action=payload.get('action')
    if action=='repair' and unit['state'] in ('ng','rejected'):
        state,code,result='return_vision4','REPAIR','RECEIVED'
    elif action=='classify' and unit['state']=='return_vision4':
        location=str(payload.get('location','')).strip()
        if not location or len(location)>100:
            raise ValueError('리턴 창고 위치를 입력하세요.')
        execute(f"UPDATE mes_units SET return_location={sql(location)} WHERE unit_id={sql(unit['unit_id'])};")
        state,code,result='return_robot4','VISION4','CLASSIFIED'
    elif action=='store' and unit['state']=='return_robot4':
        state,code,result='returned','ROBOT4','STORED'
    else:
        raise ValueError('현재 리턴 단계에 맞지 않는 작업입니다.')
    statement=event_sql(unit['unit_id'],code,result,person,str(payload.get('note',''))[:1000])
    statement+=f"UPDATE mes_units SET state={sql(state)},updated_at={sql(now())} WHERE unit_id={sql(unit['unit_id'])};"
    execute('START TRANSACTION;'+statement+'COMMIT;')
    return {'message':'수리·리턴 단계가 기록됐습니다. 공급 재고는 자동 가산하지 않습니다.'}


def korean_time(value):
    if not value: return '—'
    parsed=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')+' +09:00'


def make_pdf(snapshot, target):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.colors import HexColor
    font='MESKorean'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font,'C:/Windows/Fonts/malgun.ttf'))
    report=canvas.Canvas(str(target),pagesize=(595.28,841.89))
    report.setTitle('MES 최종검사 승인 보고서')
    def wrap(text,width,size):
        lines=[];line=''
        for character in str(text):
            if character=='\n' or pdfmetrics.stringWidth(line+character,font,size)>width:
                lines.append(line);line='' if character=='\n' else character
            else:line+=character
        if line:lines.append(line)
        return lines or ['']
    def page_header(page):
        report.setFillColor(HexColor('#14263d'));report.rect(0,742,596,100,fill=1,stroke=0)
        report.setFillColor(HexColor('#ffffff'));report.setFont(font,21)
        report.drawString(42,788,'최종검사 승인 보고서')
        report.setFont(font,10);report.drawString(42,764,'TEAM 2 / Manufacturing Execution System')
        report.setFillColor(HexColor('#65748a'));report.setFont(font,9)
        report.drawString(42,30,'학습용 · 작업자 수기 확인 기록 · PLC 주문별 자동 추적 미연결')
        report.drawRightString(554,30,str(page))
    page=1;page_header(page);y=712
    order=snapshot['order'];approval=snapshot['approval']
    for label,value in [('저장 모드',order['run_mode']),('주문번호',order['order_id']),('제품 추적번호',approval['unit_id']),('차종',order['model']),('램프 / 시트',order['lamp']+' / '+order['seat']),('승인자',approval['approver']),('승인 시각 (한국시간)',korean_time(approval['decided_at'])),('적재 상태','검사 승인 시점의 보고서 · 적재 완료 여부는 MES에서 확인')]:
        report.setFillColor(HexColor('#65748a'));report.setFont(font,10);report.drawString(42,y,label)
        report.setFillColor(HexColor('#14263d'))
        lines=wrap(value,366,10)
        for index,line in enumerate(lines):report.drawString(186,y-index*16,line)
        y-=max(24,len(lines)*16+8)
    report.setFont(font,13);report.drawString(42,y-8,'공정 확인 이력');y-=40
    for event in snapshot['events']:
        header_lines=wrap(event['stage_code']+' / '+event['result']+' / '+event['operator_name'],510,10)
        note_lines=wrap(event['note'],510,9) if event['note'] else []
        block_height=len(header_lines)*16+18+len(note_lines)*15+(3 if note_lines else 0)
        if y-block_height<72:
            report.showPage();page+=1;page_header(page);y=710
        report.setFillColor(HexColor('#14263d'));report.setFont(font,10)
        for line in header_lines:
            report.drawString(42,y,line);y-=16
        report.setFillColor(HexColor('#65748a'));report.setFont(font,9)
        report.drawString(42,y,korean_time(event['occurred_at']));y-=18
        if event['note']:
            for line in note_lines:
                if y<90:report.showPage();page+=1;page_header(page);y=710;report.setFont(font,9)
                report.drawString(42,y,line);y-=15
            y-=3
    report.save()


def decide(payload):
    person=operator(payload)
    if payload.get('role')!='admin':
        raise ValueError('관리자 화면에서 승인·반려하세요.')
    approval=one('mes_approvals',APPROVAL_FIELDS,'approval_id',payload['approval_id'])
    if approval['run_mode']!=mode(): raise ValueError('승인 요청의 저장 모드로 전환하세요.')
    if approval['state']!='waiting':
        raise ValueError('이미 처리된 승인 요청입니다.')
    action=payload.get('decision')
    if action not in ('approved','rejected'):
        raise ValueError('승인 결과를 확인하세요.')
    reason=str(payload.get('reason','')).strip()
    if len(reason)>1000 or (action=='rejected' and not reason):
        raise ValueError('반려 사유를 1~1000자로 입력하세요.')
    decided=now();unit=one('mes_units',UNIT_FIELDS,'unit_id',approval['unit_id'])
    statements=f"UPDATE mes_approvals SET state={sql(action)},decided_at={sql(decided)},approver={sql(person)},reason={sql(reason)} WHERE approval_id={sql(approval['approval_id'])};\n"
    statements+=f"UPDATE mes_units SET state={sql('running' if action=='approved' else 'rejected')},updated_at={sql(decided)} WHERE unit_id={sql(unit['unit_id'])};\n"
    target=None
    if action=='approved':
        report_id=uid();filename=report_id+'.pdf'
        approval.update(state=action,decided_at=decided,approver=person,reason=reason)
        snapshot={'order':one('mes_orders',ORDER_FIELDS,'order_id',unit['order_id']),'approval':approval,
                  'events':rows('mes_events',EVENT_FIELDS,f"unit_id={sql(unit['unit_id'])}",'ORDER BY occurred_at')}
        (BASE/'reports').mkdir(exist_ok=True)
        target=BASE/'reports'/filename
        make_pdf(snapshot,target)
        statements+=f"INSERT INTO mes_reports VALUES ({sql(report_id)},{sql(approval['approval_id'])},{sql(filename)},{sql(decided)},{sql(json.dumps(snapshot,ensure_ascii=False))},{sql(mode())});\n"
        statements+=f"INSERT INTO mes_erp_outbox VALUES ({sql(report_id)},{sql('TEST_ONLY' if mode()=='TEST' else 'WAITING_CONFIG')},{sql(decided)},{sql(mode())});\n"
    try:
        execute('START TRANSACTION;'+statements+'COMMIT;')
    except Exception:
        if target: target.unlink(missing_ok=True)
        raise
    return {'message':'승인 완료 · PDF 발행 · ERP 전송 대기' if action=='approved' else '반려 사유가 기록됐습니다.'}


def adjust_inventory(payload):
    person=operator(payload)
    reason=str(payload.get('reason','')).strip()
    if not reason or len(reason)>1000:
        raise ValueError('입출고·실사 사유를 입력하세요.')
    items=rows('mes_inventory',['item_id','quantity','capacity'],f"item_id={sql(payload['item_id'])} AND run_mode={sql(mode())}")
    if not items:raise ValueError('재고 항목을 찾을 수 없습니다.')
    item=items[0]
    quantity=int(payload['quantity'])
    if not 0<=quantity<=item['capacity']:
        raise ValueError(f'수량은 0~{item["capacity"]} 범위입니다.')
    execute(f"UPDATE mes_inventory SET quantity={quantity},updated_at={sql(now())},source='MANUAL' WHERE item_id={sql(item['item_id'])} AND run_mode={sql(mode())}; INSERT INTO mes_feedback VALUES ({sql(uid())},{sql(person)},{sql('재고 '+item['item_id']+': '+str(item['quantity'])+' → '+str(quantity)+' / '+reason)},{sql(now())},{sql(mode())});")
    return {'message':'수기 재고가 갱신됐습니다.'}


def snapshot():
    mode_where=f'run_mode={sql(mode())}'
    result={
      'orders':rows('mes_orders',ORDER_FIELDS,mode_where,order='ORDER BY created_at DESC'),
      'units':rows('mes_units',UNIT_FIELDS,mode_where,order='ORDER BY updated_at DESC'),
      'events':rows('mes_events',EVENT_FIELDS,mode_where,order='ORDER BY occurred_at DESC LIMIT 200'),
      'inventory':rows('mes_inventory',['item_id','label','quantity','capacity','updated_at','source'],mode_where),
      'approvals':rows('mes_approvals',APPROVAL_FIELDS,mode_where,order='ORDER BY requested_at DESC LIMIT 200'),
      'reports':rows('mes_reports',['report_id','approval_id','filename','created_at','run_mode'],mode_where,order='ORDER BY created_at DESC LIMIT 200'),
      'erp':rows('mes_erp_outbox',['report_id','state','created_at'],mode_where),
      'feedback':rows('mes_feedback',['feedback_id','operator_name','message','created_at'],mode_where,order='ORDER BY created_at DESC LIMIT 100'),
      'external_orders':rows('mes_external_orders',['source_order_id','mes_order_id','source_created_at','source_produced_at','source_payload'],mode_where),
      'external_history':rows('mes_external_production',['history_id','source_order_id','mes_order_id','result','completed_at'],mode_where,order='ORDER BY completed_at DESC LIMIT 30'),
      'queue':queue_rows(),'scada_outbox':rows('mes_scada_outbox',['event_id','event_type','state','created_at'],mode_where,order='ORDER BY created_at DESC LIMIT 100'),'stages':STAGES,'server_time':now(),'mode':mode()
    }
    with LIVE_LOCK: result['live']=dict(LIVE)
    evidence=execute(f"SELECT HEX(JSON_OBJECT('unit_id',unit_id,'stages',GROUP_CONCAT(DISTINCT stage_code))) FROM mes_events WHERE {mode_where} AND result='OK' GROUP BY unit_id;")
    confirmed={item['unit_id']:item['stages'].split(',') for item in [json.loads(bytes.fromhex(line.strip()).decode('utf-8')) for line in evidence.splitlines() if line.strip()]}
    seat_counts={r['unit_id']:r['count'] for r in [json.loads(bytes.fromhex(line).decode('utf-8')) for line in execute(f"SELECT HEX(JSON_OBJECT('unit_id',unit_id,'count',COUNT(*))) FROM mes_events WHERE {mode_where} AND stage_code='ROBOT2' AND result='OK' GROUP BY unit_id;").splitlines() if line.strip()]}
    for unit in result['units']:
        unit['external_source']=any(x['mes_order_id']==unit['order_id'] for x in result['external_orders'])
        unit['confirmed_stages']=confirmed.get(unit['unit_id'],[])
        unit['seat_completed']=seat_counts.get(unit['unit_id'],0)
        order=next(o for o in result['orders'] if o['order_id']==unit['order_id'])
        unit['seat_required']=2 if order['model']=='VAN' else 1
        if unit['seat_completed']<unit['seat_required']:
            unit['confirmed_stages']=[c for c in unit['confirmed_stages'] if c not in ('VISION2','ROBOT2')]
    try: result['scada_sync']=json.loads((BASE/'scada-sync-status.json').read_text(encoding='utf-8'))
    except Exception: result['scada_sync']={'connected':False,'error':'연동 확인 중'}
    heartbeat=MANUFACTURING/'heartbeat.json'
    try:
        value=json.loads(heartbeat.read_text());value['fresh']=time.time()-value['time']<5
        result['collector']=value
    except Exception:
        result['collector']={'connected':False,'fresh':False}
    return result


def poll_live():
    while True:
        try:
            tags={}
            for table in ['team2-robotdb','team2-vision','team2-order','team2-plcstatus']:
                tags[table]=rows(table,['tag_name','value_text','value_number','status_code','received_at','collection_mode'])
            with LIVE_LOCK: LIVE.update(connected=True,error='',tags=tags,checked_at=now())
        except Exception as exc:
            logging.exception('Live database read failed')
            with LIVE_LOCK: LIVE.update(connected=False,error=str(exc),checked_at=now())
        time.sleep(2)


ALLOWED_CLIENTS={'127.0.0.1'}
ALLOWED_ORIGINS={'http://127.0.0.1:8765','http://localhost:8765'}

class Handler(BaseHTTPRequestHandler):
    def client_allowed(self):
        if self.client_address[0] in ALLOWED_CLIENTS: return True
        self.send({'error':'접속이 허용된 PC가 아닙니다.'},403)
        return False

    def log_message(self,format,*args):
        logging.info(format,*args)

    def send(self,content,status=200,mime='application/json; charset=utf-8'):
        if isinstance(content,(dict,list)): content=json.dumps(content,ensure_ascii=False).encode('utf-8')
        elif isinstance(content,str): content=content.encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(content)))
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(content)

    def do_GET(self):
        if not self.client_allowed(): return
        path=urlparse(self.path).path
        try:
            if path=='/api/state':
                with MUTATION_LOCK: self.send(snapshot())
            elif path.startswith('/reports/'):
                filename=path.split('/')[-1]
                if not filename.endswith('.pdf') or any(c not in '0123456789abcdef-.pdf' for c in filename):
                    self.send({'error':'파일을 찾을 수 없습니다.'},404);return
                file=BASE/'reports'/filename
                if not file.is_file():self.send({'error':'파일을 찾을 수 없습니다.'},404);return
                self.send(file.read_bytes(),mime='application/pdf')
            elif path in ('/','/index.html','/app.js','/style.css'):
                name='index.html' if path=='/' else path.lstrip('/')
                mime={'html':'text/html','js':'text/javascript','css':'text/css'}[name.rsplit('.',1)[1]]+'; charset=utf-8'
                self.send((BASE/name).read_bytes(),mime=mime)
            else:self.send({'error':'경로를 찾을 수 없습니다.'},404)
        except Exception as exc:
            logging.exception('Request failed');self.send({'error':str(exc)},500)

    def do_POST(self):
        if not self.client_allowed(): return
        try:
            origin=self.headers.get('Origin')
            if origin and origin not in ALLOWED_ORIGINS:
                raise ValueError('이 화면에서 다시 요청하세요.')
            size=int(self.headers.get('Content-Length',0))
            if size>16384:raise ValueError('입력이 너무 큽니다.')
            payload=json.loads(self.rfile.read(size))
            handlers={'/api/orders':create_order,'/api/advance':advance,'/api/resupply':resupply,'/api/return':return_action,
                      '/api/approval':decide,'/api/inventory':adjust_inventory,'/api/mode':set_mode}
            with MUTATION_LOCK:
                if self.path=='/api/feedback':
                    person=operator(payload);message=str(payload.get('message','')).strip()
                    if not message or len(message)>2000:raise ValueError('피드백을 1~2000자로 입력하세요.')
                    execute(f'INSERT INTO mes_feedback VALUES ({sql(uid())},{sql(person)},{sql(message)},{sql(now())},{sql(mode())});')
                    result={'message':'피드백이 등록됐습니다.'}
                elif self.path in handlers:result=handlers[self.path](payload)
                else:raise ValueError('작업 경로를 확인하세요.')
                self.send(result)
        except ValueError as exc:self.send({'error':str(exc)},400)
        except Exception as exc:
            logging.exception('Mutation failed');self.send({'error':str(exc)},500)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--init-db',action='store_true');args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',handlers=[RotatingFileHandler(BASE/'mes.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')])
    if args.init_db:
        execute((BASE/'schema.sql').read_text(encoding='utf-8'));print('MES schema initialized')
    else:
        import sys,scada_sync
        threading.Thread(target=scada_sync.run,args=(sys.modules[__name__],),daemon=True).start()
        threading.Thread(target=poll_live,daemon=True).start()
        print('MES http://127.0.0.1:8765',flush=True)
        local_server=ThreadingHTTPServer(('127.0.0.1',8765),Handler)
        local_server.serve_forever()
