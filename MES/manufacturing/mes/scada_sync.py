"""Read-only smart_factory -> MES, preserving source IDs and GUI mode at first import."""
import datetime as dt
import json
import logging
from pathlib import Path
import subprocess
import time

BASE=Path(__file__).resolve().parent
CONFIG=BASE/'scada-source.json'
STATUS=BASE/'scada-sync-status.json'
KST=dt.timezone(dt.timedelta(hours=9))

def source_rows(cfg, statement):
    result=subprocess.run([cfg['source_mysql_exe'],f'--defaults-file={BASE/"scada-client.ini"}',
        '--get-server-public-key','--connect-timeout=5','--batch','--skip-column-names'],input=statement,
        encoding='utf-8',capture_output=True,timeout=25,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode: raise RuntimeError(result.stderr.strip())
    return [json.loads(bytes.fromhex(line).decode('utf-8')) for line in result.stdout.splitlines() if line.strip()]

def utc(value):
    if not value:return None
    parsed=dt.datetime.fromisoformat(value)
    return parsed.replace(tzinfo=KST).astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')

def option(row):
    name=row['product_name'].upper()
    model='VAN' if name.startswith('VAN-') else 'TRUCK' if name.startswith('TRUCK-') else None
    seat='COCOA' if '-COCOA-' in name else 'DARK' if '-BROWN-' in name else None
    lamp={'A':'ROUND','B':'ANGULAR'}.get(name.rsplit('-',1)[-1])
    if not all((model,seat,lamp)):raise ValueError('Unknown source product: '+name)
    return model,seat,lamp

def sync_once(mes):
    cfg=json.loads(CONFIG.read_text(encoding='utf-8'))
    if not cfg.get('enabled'): return {'enabled':False}
    orders=source_rows(cfg,"""SELECT HEX(JSON_OBJECT('order_id',o.order_id,'product_code',o.product_code,
        'order_qty',o.order_qty,'completed_qty',o.completed_qty,'defect_qty',o.defect_qty,'status',o.status,
        'order_time',o.order_time,'start_time',o.start_time,'complete_time',o.complete_time,
        'product_name',p.product_name,'body_type',p.body_type,'seat_type',p.seat_type,'light_type',p.light_type))
        FROM smart_factory.orders o LEFT JOIN smart_factory.product_master p ON p.product_code=o.product_code
        ORDER BY COALESCE(o.start_time,o.order_time),o.order_id;""")
    if 'bootstrap_order_ids' not in cfg:
        cfg['bootstrap_order_ids']=[str(o['order_id']) for o in orders]
        cfg['bootstrap_at']=mes.now()
        tmp=CONFIG.with_suffix('.tmp');tmp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(CONFIG)
    backlog=set(cfg['bootstrap_order_ids'])
    with mes.MUTATION_LOCK:
        mappings={r['source_order_id']:r for r in mes.rows('mes_external_orders',
            ['source_order_id','mes_order_id','run_mode','source_payload'],"source_name='smart_factory'")}
        imported=0;updated=0
        for row in orders:
            key=str(row['order_id']);encoded=json.dumps(row,ensure_ascii=False,sort_keys=True)
            mapped=mappings.get(key)
            if mapped and mapped['source_payload']==encoded: continue
            if not mapped:
                model,seat,lamp=option(row)
                timestamp=row['start_time'] or row['order_time']
                stamp=dt.datetime.fromisoformat(timestamp).strftime('%Y%m%d-%H%M%S')
                identifier=stamp+'-'+model+'-'+('C' if seat=='COCOA' else 'B')+'-'+('A' if lamp=='ROUND' else 'B')+'-S'+key
                run_mode='TEST' if key in backlog else mes.mode()
                values=[identifier,model,lamp,seat,row['order_qty'],None,utc(row['order_time']),'SCADA 가져오기',run_mode]
                statement='INSERT INTO mes_orders ('+','.join(mes.ORDER_FIELDS)+') VALUES ('+','.join(mes.sql(v) for v in values)+');'
                for index in range(1,row['order_qty']+1):
                    unit=identifier+f'-{index:03d}'
                    state='completed' if index<=row['completed_qty'] else 'external_running' if row['status']=='RUNNING' else 'planned'
                    stage=8 if state=='completed' else 0
                    statement+=f"INSERT INTO mes_units VALUES ({mes.sql(unit)},{mes.sql(identifier)},{stage},{mes.sql(state)},NULL,{mes.sql(utc(row['complete_time'] or timestamp))},{mes.sql(run_mode)});"
                    # Old completed records do not imply warehouse stock or historical stage confirmations.
                    if state=='planned':
                        maximum=mes.rows('mes_dispatch_queue',['position'],f"run_mode={mes.sql(run_mode)} AND state='waiting'",'ORDER BY position DESC LIMIT 1')
                        position=(maximum[0]['position'] if maximum else 0)+index*10
                        statement+=f"INSERT INTO mes_dispatch_queue VALUES ({mes.sql(unit)},{position},'waiting','NORMAL',NULL,{mes.sql(mes.now())},{mes.sql(run_mode)});"
                fields=['source_name','source_order_id','run_mode','mes_order_id','source_created_at','source_produced_at','source_timezone','source_payload','imported_at']
                vals=['smart_factory',key,run_mode,identifier,utc(row['order_time']),utc(row['start_time']), 'Asia/Seoul',encoded,mes.now()]
                statement+='INSERT INTO mes_external_orders ('+','.join(fields)+') VALUES ('+','.join(mes.sql(v) for v in vals)+');'
                mes.execute('START TRANSACTION;'+statement+'COMMIT;')
                mappings[key]={'mes_order_id':identifier,'run_mode':run_mode,'source_payload':encoded}
                imported+=1
            else:
                # Source truth is synchronized without overwriting MES manual/repair execution.
                mes.execute(f"UPDATE mes_external_orders SET source_payload={mes.sql(encoded)},source_produced_at={mes.sql(utc(row['start_time']))} WHERE source_name='smart_factory' AND source_order_id={mes.sql(key)};")
                # Only synchronize untouched imported units; never replace manual MES execution.
                statement=''
                for index in range(1,row['order_qty']+1):
                    unit=mapped['mes_order_id']+f'-{index:03d}'
                    if index<=row['completed_qty']:
                        stamp=utc(row['complete_time'] or row['start_time'] or row['order_time'])
                        statement+=f"UPDATE mes_units u SET state='completed',stage=8,updated_at={mes.sql(stamp)} WHERE unit_id={mes.sql(unit)} AND state IN ('planned','external_running') AND NOT EXISTS(SELECT 1 FROM mes_events e WHERE e.unit_id=u.unit_id);"
                        statement+=f"UPDATE mes_dispatch_queue q JOIN mes_units u ON u.unit_id=q.unit_id SET q.state='completed' WHERE q.unit_id={mes.sql(unit)} AND u.state='completed';"
                if statement:mes.execute('START TRANSACTION;'+statement+'COMMIT;')
                updated+=1
        history=source_rows(cfg,"""SELECT HEX(JSON_OBJECT('history_id',history_id,'order_id',order_id,'product_code',product_code,'result',result,'complete_time',complete_time)) FROM smart_factory.production_history ORDER BY history_id;""")
        existing={r['history_id'] for r in mes.rows('mes_external_production',['history_id'],"source_name='smart_factory'")}
        statements=[]
        for row in history:
            mapping=mappings.get(str(row['order_id']))
            if not mapping or str(row['history_id']) in existing:continue
            vals=['smart_factory',str(row['history_id']),str(row['order_id']),mapping['mes_order_id'],row['result'],utc(row['complete_time']),json.dumps(row,ensure_ascii=False),mapping['run_mode']]
            statements.append('INSERT INTO mes_external_production VALUES ('+','.join(mes.sql(v) for v in vals)+');')
        if statements:mes.execute('START TRANSACTION;'+''.join(statements)+'COMMIT;')
    status={'enabled':True,'connected':True,'checked_at':mes.now(),'source_orders':len(orders),'linked_orders':len(mappings),'imported':imported,'updated':updated,'history_count':len(history),'bootstrap_count':len(backlog),'error':''}
    STATUS.write_text(json.dumps(status,ensure_ascii=False),encoding='utf-8')
    return status

def run(mes):
    while True:
        try:sync_once(mes)
        except Exception as exc:
            logging.exception('SCADA import failed')
            STATUS.write_text(json.dumps({'enabled':True,'connected':False,'checked_at':mes.now(),'error':str(exc)},ensure_ascii=False),encoding='utf-8')
        time.sleep(3)
