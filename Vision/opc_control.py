"""Core's asyncio loop owns every OPC client and inspection task."""
import asyncio
import threading
from urllib.parse import urlsplit


class OpcController:
    def __init__(self, loop, config, stations, states, cameras, stop, renderer,
                 io_factory, cycle, logger, client_factory=None):
        self.loop, self.config, self.stations = loop, config, stations
        self.states, self.cameras, self.stop = states, cameras, stop
        self.renderer, self.io_factory, self.cycle = renderer, io_factory, cycle
        self.logger, self.client_factory = logger, client_factory
        self.client = None
        self.tasks = []
        self.lock = threading.Lock()
        self.info = dict(connected=False, busy=False, write_enabled=False,
                         endpoint=config['endpoint'], error='', status='미연결')
        self.command_future = None

    def snapshot(self):
        with self.lock:
            return dict(self.info)

    def update(self, **values):
        with self.lock:
            self.info.update(values)

    def submit(self, action, data):
        if action not in ('connect', 'disconnect'):
            raise ValueError('지원하지 않는 OPC 명령입니다')
        endpoint = str(data.get('endpoint', self.config['endpoint'])).strip()
        write = data.get('write', False)
        if not isinstance(write, bool):
            raise ValueError('쓰기 모드는 true/false만 가능합니다')
        if action == 'connect':
            parsed = urlsplit(endpoint)
            if parsed.scheme != 'opc.tcp' or not parsed.hostname or not parsed.port:
                raise ValueError('opc.tcp://IP:포트 형식으로 입력하세요')
            if write and data.get('confirmed') is not True:
                raise ValueError('카메라·모델·태그·PLC 확인 후 쓰기 운전을 확인하세요')
        with self.lock:
            if self.info['busy']:
                raise ValueError('OPC 명령 처리 중입니다. 완료 후 다시 시도하세요')
            if action == 'connect' and self.info['connected']:
                raise ValueError('먼저 OPC 연결을 해제한 뒤 모드를 변경하세요')
            self.info.update(busy=True, error='', status='연결 중' if action == 'connect' else '해제 중')
        self.command_future = asyncio.run_coroutine_threadsafe(self.execute(action, endpoint, write), self.loop)

    async def disconnect(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        client, self.client = self.client, None
        self.renderer.write_enabled = False
        self.update(connected=False, write_enabled=False)
        if client:
            await client.disconnect()

    async def execute(self, action, endpoint, write):
        self.update(busy=True)
        try:
            await self.disconnect()
            if action == 'disconnect':
                for state in self.states.values():
                    state.update(status='OPC 미연결', detail='연결 해제: PLC 출력은 자동 초기화하지 않습니다.')
                self.update(status='미연결')
                return
            factory = self.client_factory
            if factory is None:
                from asyncua import Client
                factory = Client
            self.client = factory(endpoint, timeout=3)
            await self.client.connect()
            ios = [self.io_factory(self.client, s, self.states[i], write, self.logger)
                   for i, s in self.stations.items()]
            # Check all tag qualities/types/permissions before starting any cycle.
            for io in ios:
                await io.prepare()
            self.renderer.write_enabled = write
            self.update(connected=True, endpoint=endpoint, write_enabled=write,
                        status='연결됨 / 실제 쓰기' if write else '연결됨 / 읽기 시험')
            self.tasks = [asyncio.create_task(self.cycle(io, self.cameras[io.station['id']],
                                                       self.config, self.stop)) for io in ios]
            self.tasks.append(asyncio.create_task(self.monitor(ios[0])))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                await self.disconnect()
            except Exception:
                pass
            self.update(status='연결 실패', error=f'{type(exc).__name__}: {exc}')
        finally:
            self.update(busy=False)

    async def monitor(self, io):
        try:
            while not self.stop.is_set():
                await asyncio.sleep(1)
                await io.read('start')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.tasks.remove(asyncio.current_task())
            try:
                await self.disconnect()
            finally:
                self.update(status='통신 끊김', error=str(exc))

    async def shutdown(self):
        if self.command_future and not self.command_future.done():
            self.command_future.cancel()
            await asyncio.gather(asyncio.wrap_future(self.command_future), return_exceptions=True)
        await self.disconnect()
