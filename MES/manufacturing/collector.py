"""Read-only OPC UA subscription -> durable SQLite outbox -> MySQL CLI."""
import argparse
import asyncio
import base64
import dataclasses
import datetime as dt
import json
import logging
from logging.handlers import RotatingFileHandler
import math
from pathlib import Path
import sqlite3
import subprocess
import uuid
import time

from asyncua import Client, ua

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger('collector')


def collection_mode():
    try:
        value=json.loads((BASE/'mes/mode.json').read_text(encoding='utf-8'))['mode']
        return value if value in ('TEST','PRODUCTION') else 'LEGACY'
    except Exception:
        return 'LEGACY'


def heartbeat(connected):
    temp=BASE/'heartbeat.tmp'
    temp.write_text(json.dumps({'connected':connected,'time':time.time(),'mode':collection_mode()}),encoding='utf-8')
    temp.replace(BASE/'heartbeat.json')


def normalize(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {'nonfinite': str(value)}
    if isinstance(value, bytes):
        return {'base64': base64.b64encode(value).decode('ascii')}
    if isinstance(value, (dt.datetime, dt.date, uuid.UUID)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    if dataclasses.is_dataclass(value):
        return normalize(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    return {'type': type(value).__name__, 'text': str(value)}


def timestamp(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')


def literal(value):
    # Hex literals keep tag names and values independent of SQL escaping modes.
    if value is None:
        return 'NULL'
    if isinstance(value, (int, float)):
        return str(value)
    return "CONVERT(X'" + str(value).encode('utf-8').hex() + "' USING utf8mb4) COLLATE utf8mb4_unicode_ci"


def insert_source_sql(rows, table, root):
    rows=[[*row[:9],row[9] if len(row)>9 else 'LEGACY'] for row in rows]
    values = ',\n'.join('(' + ','.join(literal(v) for v in row) + ')' for row in rows)
    history = ('USE `opcua-manufacturing`;\nSTART TRANSACTION;\n'
               f'INSERT INTO `{table}-history` (event_id,endpoint,node_id,received_at,source_at,server_at,status_code,variant_type,value_json,collection_mode) VALUES\n'
               + values + '\nON DUPLICATE KEY UPDATE event_id=VALUES(event_id);\n')
    latest_rows = []
    for row in rows:
        value = json.loads(row[8])
        number = float(value) if isinstance(value, (int, float, bool)) else None
        if number is not None and not math.isfinite(number):
            number = None
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        name = row[2].removeprefix(root + '.')
        latest_rows.append([row[2], name, text, number, row[7], row[6], row[4], row[5], row[3], row[0],row[9]])
    latest_values = ',\n'.join('(' + ','.join(literal(v) for v in row) + ')' for row in latest_rows)
    fields = ['tag_name','value_text','value_number','variant_type','status_code','source_at','server_at','event_id','collection_mode']
    update = ','.join(f'{f}=IF(received_at<=VALUES(received_at),VALUES({f}),{f})' for f in fields)
    update += ',received_at=GREATEST(received_at,VALUES(received_at))'
    return history + (f'INSERT INTO `{table}` (node_id,tag_name,value_text,value_number,variant_type,status_code,source_at,server_at,received_at,event_id,collection_mode) VALUES\n'
                     + latest_values + '\nON DUPLICATE KEY UPDATE ' + update + ';\nCOMMIT;\n')


def insert_sql(rows, sources=None):
    sources = sources or [{'root_node': 'ns=2;s=M.PLC.ROBOT_I/O', 'table': 'team2-robotdb'}]
    groups = {source['table']: [] for source in sources}
    for source in sources:
        if not source['table'] or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in source['table']):
            raise ValueError('Invalid table name in configuration')
    for row in rows:
        matches = [s for s in sources if row[2] == s['root_node'] or row[2].startswith(s['root_node'] + '.')]
        if len(matches) != 1:
            raise ValueError(f'Cannot route node: {row[2]}')
        groups[matches[0]['table']].append(row)
    return ''.join(insert_source_sql(groups[s['table']], s['table'], s['root_node']) for s in sources if groups[s['table']])


async def discover_sources(client, config):
    sources = config.get('sources', [{'root_node': config['root_node'], 'table': 'team2-robotdb'}])
    nodes = []
    for source in sources:
        found = await discover(client, source['root_node'])
        if not found:
            raise RuntimeError(f"No variables found for {source['table']}")
        LOG.info('Discovered %d nodes for %s', len(found), source['table'])
        nodes.extend(found)
    return nodes


def mysql(config, sql):
    options = (BASE / config['mysql_options_file']).resolve()
    if not options.is_file():
        raise FileNotFoundError(f'MySQL options file missing: {options}')
    result = subprocess.run(
        [config['mysql_exe'], f'--defaults-file={options}',
         '--batch', '--skip-column-names', '--connect-timeout=5'],
        input=sql, encoding='utf-8', capture_output=True, timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


class Outbox:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit()

    def put(self, row):
        with self.db:
            self.db.execute('INSERT INTO pending VALUES (?,?)', (row[0], json.dumps(row)))

    def batch(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM pending ORDER BY rowid LIMIT 200')]

    def acknowledge(self, rows):
        with self.db:
            self.db.executemany('DELETE FROM pending WHERE id=?', [(r[0],) for r in rows])


class Handler:
    def __init__(self, config, outbox):
        self.config, self.outbox = config, outbox
        self.failed = None

    def datachange_notification(self, node, value, data):
        try:
            dv = data.monitored_item.Value
            self.outbox.put([
                str(uuid.uuid4()), self.config['endpoint'], node.nodeid.to_string(),
                timestamp(dt.datetime.now(dt.timezone.utc)), timestamp(dv.SourceTimestamp),
                timestamp(dv.ServerTimestamp), dv.StatusCode.value,
                dv.Value.VariantType.name if dv.Value else 'Null',
                json.dumps(normalize(value), ensure_ascii=False, allow_nan=False),collection_mode()])
        except Exception as exc:
            self.failed = exc
            LOG.exception('Local persistence failed; collection will stop')

    def status_change_notification(self, status):
        LOG.warning('Subscription status: %s', status)
        if status.Status.is_bad():
            self.failed = RuntimeError(str(status))


async def discover(client, root):
    pending, seen, nodes = [client.get_node(root)], set(), []
    while pending:
        node = pending.pop()
        key = node.nodeid.to_string()
        if key in seen:
            continue
        seen.add(key)
        kind = await node.read_node_class()
        if kind == ua.NodeClass.Variable:
            nodes.append(node)
        elif kind == ua.NodeClass.Object:
            pending.extend(await node.get_children())
    return nodes


async def writer(config, outbox):
    while True:
        rows = outbox.batch()
        if rows:
            try:
                await asyncio.to_thread(mysql, config, insert_sql(rows, config.get('sources')))
                outbox.acknowledge(rows)
                LOG.info('Committed %d samples', len(rows))
            except Exception as exc:
                LOG.error('MySQL unavailable; retained batch: %s', exc)
                await asyncio.sleep(5)
        await asyncio.sleep(0.5)


async def collect(config, outbox):
    while True:
        handler = Handler(config, outbox)
        try:
            async with Client(config['endpoint'], timeout=10) as client:
                nodes = await discover_sources(client, config)
                if not nodes:
                    raise RuntimeError('No variable nodes found')
                sub = await client.create_subscription(config['publish_ms'], handler)
                handles = await sub.subscribe_data_change(nodes, queuesize=100, sampling_interval=config['publish_ms'])
                failures = [(n.nodeid.to_string(), str(h)) for n, h in zip(nodes, handles) if isinstance(h, ua.StatusCode)]
                if failures:
                    raise RuntimeError(f'Subscription failures: {failures}')
                LOG.info('Subscribed to %d nodes', len(nodes))
                active_mode=collection_mode()
                while True:
                    await asyncio.sleep(1)
                    if handler.failed:
                        raise handler.failed
                    await client.check_connection()
                    if collection_mode()!=active_mode:
                        await sub.delete()
                        sub=await client.create_subscription(config['publish_ms'],handler)
                        handles=await sub.subscribe_data_change(nodes,queuesize=100,sampling_interval=config['publish_ms'])
                        failures=[str(h) for h in handles if isinstance(h,ua.StatusCode)]
                        if failures:raise RuntimeError('Mode change subscription failed: '+str(failures))
                        active_mode=collection_mode()
                        LOG.info('Collection mode changed to %s; captured new initial values',active_mode)
                    heartbeat(True)
        except (sqlite3.Error, OSError) as exc:
            heartbeat(False)
            if handler.failed is exc:
                raise
            LOG.exception('Connection interrupted; retrying in 5 seconds')
        except Exception:
            heartbeat(False)
            if handler.failed:
                raise
            LOG.exception('OPC UA failed; retrying in 5 seconds')
        await asyncio.sleep(5)


async def run(config, args):
    if args.probe:
        async with Client(config['endpoint'], timeout=10) as client:
            nodes = await discover_sources(client, config)
            result = []
            for node in nodes:
                dv = await node.read_data_value(raise_on_bad_status=False)
                result.append({'node_id': node.nodeid.to_string(), 'status': str(dv.StatusCode),
                               'type': dv.Value.VariantType.name if dv.Value else 'Null'})
            (BASE / 'node_inventory.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'Discovered {len(result)} variables; saved node_inventory.json')
        return
    if args.init_db:
        config = dict(config)
        if (BASE / 'mysql-admin.ini').exists():
            config['mysql_options_file'] = 'mysql-admin.ini'
        print(await asyncio.to_thread(mysql, config, (BASE / 'schema.sql').read_text(encoding='utf-8')))
        print('Database initialized')
        return
    outbox = Outbox(BASE / config['spool'])
    tasks = [asyncio.create_task(collect(config, outbox))]
    if not args.capture_only:
        tasks.append(asyncio.create_task(writer(config, outbox)))
    async def stop_requested():
        while not (BASE / 'collector.stop').exists():
            await asyncio.sleep(1)

    stop_task = asyncio.create_task(stop_requested())
    try:
        if args.seconds:
            async with asyncio.timeout(args.seconds):
                done, _ = await asyncio.wait([*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED)
        else:
            done, _ = await asyncio.wait([*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except TimeoutError:
        pass
    finally:
        for task in [*tasks, stop_task]:
            task.cancel()
        await asyncio.gather(*tasks, stop_task, return_exceptions=True)
        print('Pending samples:', outbox.db.execute('SELECT count(*) FROM pending').fetchone()[0])
        outbox.db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--init-db', action='store_true')
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--capture-only', action='store_true')
    parser.add_argument('--seconds', type=float)
    args = parser.parse_args()
    log_handler = RotatingFileHandler(BASE / 'collector.log', maxBytes=5_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[log_handler])
    logging.getLogger('asyncua').setLevel(logging.WARNING)
    config = json.loads((BASE / 'config.json').read_text(encoding='utf-8'))
    if not args.probe and not args.init_db:
        import msvcrt
        lock_file = (BASE / 'collector.lock').open('a+b')
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('Another collector is already running')
        (BASE / 'collector.stop').unlink(missing_ok=True)
    try:
        asyncio.run(run(config, args))
    except KeyboardInterrupt:
        pass
