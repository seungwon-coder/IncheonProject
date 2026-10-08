"""휴대폰에서 Dobot 상태·JOG·티칭 포인트 저장을 사용하는 모바일 웹 화면.

모바일 제어권과 JOG watchdog을 사용하며, 현재 좌표 저장은 로봇을 움직이지 않는다.
PTP 포인트 이동과 HOME은 별도 안전 확인 단계가 끝날 때까지 제공하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.parse import parse_qs
from pathlib import Path

from robot_control.dobot_config import ROBOT_NAMES
from integrated_gui.integrated_robot_status import IntegratedRobotStatusService, RobotLiveStatus
from teaching.teaching_points import (
    DEFAULT_TEACHING_FILE,
    load_teaching_data,
    save_teaching_data,
    teach_point,
)


CONTROL_LEASE_SECONDS = 15.0


class ControlLease:
    """여러 휴대폰이 한 로봇을 동시에 조작하지 못하게 제어권을 관리한다.

    아직 로봇 이동 명령은 없지만, 이후 JOG 기능이 이 제어권 토큰을 반드시
    확인하도록 만들 수 있다. 휴대폰의 heartbeat가 끊기면 제어권은 자동 만료된다.
    """

    def __init__(self, timeout_seconds: float = CONTROL_LEASE_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._client_id: str | None = None
        self._robot: str | None = None
        self._expires_at = 0.0

    def _expire_if_needed(self) -> None:
        if self._token is not None and self._clock() >= self._expires_at:
            self._token = self._client_id = self._robot = None
            self._expires_at = 0.0

    def acquire(self, client_id: str, robot: str) -> dict[str, Any]:
        if not client_id.strip():
            raise ValueError("client_id가 비어 있습니다.")
        if robot not in ROBOT_NAMES:
            raise ValueError(f"등록되지 않은 로봇입니다: {robot}")
        with self._lock:
            self._expire_if_needed()
            if self._token is not None:
                raise RuntimeError(f"{self._robot} 제어권을 다른 화면에서 사용 중입니다.")
            self._token = secrets.token_urlsafe(24)
            self._client_id = client_id
            self._robot = robot
            self._expires_at = self._clock() + self.timeout_seconds
            return {**self._public_snapshot(), "token": self._token}

    def heartbeat(self, token: str) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            self._require_token(token)
            self._expires_at = self._clock() + self.timeout_seconds
            return self._public_snapshot()

    def release(self, token: str) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            self._require_token(token)
            self._token = self._client_id = self._robot = None
            self._expires_at = 0.0
            return self._public_snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            return self._public_snapshot()

    def robot_for(self, token: str) -> str:
        """유효한 제어권 토큰이 잠근 로봇 이름을 반환한다."""
        with self._lock:
            self._expire_if_needed()
            self._require_token(token)
            assert self._robot is not None
            return self._robot

    def _require_token(self, token: str) -> None:
        if self._token is None or not secrets.compare_digest(self._token, token):
            raise PermissionError("유효한 제어권이 없습니다.")

    def _public_snapshot(self) -> dict[str, Any]:
        remaining = max(0.0, self._expires_at - self._clock()) if self._token else 0.0
        return {"active": self._token is not None, "robot": self._robot,
                "expires_in": round(remaining, 1)}


PAGE_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Dobot 모바일 티칭</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { margin: 0; background: #111827; color: #f9fafb; }
    header { position: sticky; top: 0; padding: 14px; background: #1f2937; }
    h1 { margin: 0; font-size: 1.25rem; }
    .notice { margin-top: 6px; color: #fbbf24; font-weight: 700; }
    main { display: grid; gap: 12px; padding: 12px; }
    .card { border-radius: 12px; padding: 14px; background: #1f2937; }
    .card h2 { margin: 0 0 10px; font-size: 1.1rem; }
    .ok { color: #4ade80; } .bad { color: #f87171; }
    .pose { font-family: ui-monospace, monospace; line-height: 1.7; }
    .control { margin: 12px; border: 2px solid #374151; }
    .jog-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 7px; margin-top: 8px; }
    .jog-grid, .jog-grid button {
      /* 휴대폰의 길게 누르기 텍스트 선택과 복사 메뉴를 막는다. */
      user-select: none; -webkit-user-select: none; -webkit-touch-callout: none;
    }
    .jog-grid button {
      display: block; width: 100%; min-width: 0; margin: 0; touch-action: none;
      background: #2563eb; color: white; cursor: pointer;
    }
    .stop { width: 100%; background: #dc2626; color: white; }
    .teach-row { display: grid; grid-template-columns: 1fr 110px; gap: 8px; }
    .teach-row select, .teach-row input { width: 100%; box-sizing: border-box; }
    select, button { min-height: 44px; margin: 4px; padding: 8px 12px; font-size: 1rem; }
    button { border: 0; border-radius: 8px; font-weight: 700; }
    footer { padding: 12px; color: #9ca3af; text-align: center; }
    @media (min-width: 700px) { main { grid-template-columns: 1fr 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>Dobot 4대 모바일 상태</h1>
    <div class="notice">JOG 사용 전 로봇과 주변 설비의 간섭 여부를 확인하세요.</div>
  </header>
  <section class="card control">
    <h2>모바일 제어권</h2>
    <select id="robot-select">
      <option>Dobot_1</option><option>Dobot_2</option>
      <option>Dobot_3</option><option>Dobot_4</option>
    </select>
    <button id="acquire">제어권 획득</button>
    <button id="release">제어권 반납</button>
    <div id="control-state">제어권 상태를 읽는 중...</div>
    <div class="notice">JOG 버튼은 누르는 동안 실제 로봇이 움직입니다. 주변 간섭을 먼저 확인하세요.</div>
    <label>JOG 속도 <input id="jog-speed" type="number" min="1" max="100" value="5"> %</label>
    <h3>직교좌표</h3><div class="jog-grid" id="cartesian-jog"></div>
    <h3>관절축</h3><div class="jog-grid" id="joint-jog"></div>
    <button class="stop" id="jog-stop">JOG 즉시 정지</button>
    <h3>티칭 포인트 저장</h3>
    <div class="teach-row">
      <select id="point-select"></select>
      <input id="teach-speed" type="number" min="1" max="100" value="5" aria-label="티칭 속도 비율">
    </div>
    <button id="save-point">현재 위치 저장/수정</button>
    <div id="point-state">로봇을 선택하면 포인트 목록을 읽습니다.</div>
  </section>
  <main id="robots"></main>
  <footer id="updated">상태를 읽는 중...</footer>
  <script>
    const names = ["Dobot_1", "Dobot_2", "Dobot_3", "Dobot_4"];
    // 일반 HTTP로 접속한 휴대폰에서는 crypto.randomUUID()가 없을 수 있다.
    // 따라서 모든 브라우저에서 동작하는 시간+난수 방식도 함께 준비한다.
    const makeClientId = () => {
      if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
        return globalThis.crypto.randomUUID();
      }
      return `phone-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    };
    const clientId = sessionStorage.getItem('dobotClientId') || makeClientId();
    sessionStorage.setItem('dobotClientId', clientId);
    let controlToken = sessionStorage.getItem('dobotControlToken');
    let jogHeld = false;
    let jogTimer = null;
    const post = async (path, data) => {
      const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      return result;
    };
    function showControl(data) {
      document.querySelector('#control-state').textContent = data.active
        ? `${data.robot} 제어권 사용 중 (자동 해제까지 약 ${data.expires_in}초)` : '사용 가능한 상태';
    }
    async function loadPoints() {
      const robot = document.querySelector('#robot-select').value;
      try {
        const response = await fetch(`/api/points?robot=${encodeURIComponent(robot)}`, {cache: 'no-store'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        const select = document.querySelector('#point-select');
        select.innerHTML = data.points.map(item =>
          `<option value="${item.name}">${item.name}${item.valid ? ' (등록)' : ' (미등록)'}</option>`
        ).join('');
        showPoint(data.points.find(item => item.name === select.value));
        select.onchange = () => showPoint(data.points.find(item => item.name === select.value));
      } catch (error) {
        document.querySelector('#point-state').textContent = `포인트 읽기 실패: ${error.message}`;
      }
    }
    function showPoint(point) {
      document.querySelector('#point-state').textContent = !point ? '포인트 없음' :
        point.valid ? `${point.name}: X=${number(point.pose.x)}, Y=${number(point.pose.y)}, Z=${number(point.pose.z)}, R=${number(point.pose.r)} / ${point.speed_percent}%` :
        `${point.name}: 미등록`;
    }
    async function refreshControl() {
      try { showControl(await (await fetch('/api/control', {cache: 'no-store'})).json()); } catch (_) {}
    }
    document.querySelector('#acquire').onclick = async () => {
      try {
        const data = await post('/api/control/acquire', {client_id: clientId, robot: document.querySelector('#robot-select').value});
        controlToken = data.token;
        sessionStorage.setItem('dobotControlToken', controlToken);
        showControl(data);
        await loadPoints();
      } catch (error) { alert(error.message); }
    };
    document.querySelector('#release').onclick = async () => {
      if (!controlToken) return alert('이 화면이 가진 제어권이 없습니다.');
      try {
        await stopJog();
        const data = await post('/api/control/release', {token: controlToken});
        controlToken = null; sessionStorage.removeItem('dobotControlToken'); showControl(data);
      } catch (error) { controlToken = null; sessionStorage.removeItem('dobotControlToken'); alert(error.message); }
    };
    async function stopJog() {
      jogHeld = false;
      if (jogTimer) { clearInterval(jogTimer); jogTimer = null; }
      if (!controlToken) return;
      try { await post('/api/jog/stop', {token: controlToken}); }
      catch (error) { console.warn(error); }
    }
    async function startJog(axis, direction) {
      if (!controlToken || jogHeld) return;
      jogHeld = true;
      try {
        const speed = Number(document.querySelector('#jog-speed').value);
        await post('/api/jog/start', {token: controlToken, axis, direction, speed_percent: speed});
        // 0.25초 Worker 감시가 끝나기 전에 0.10초마다 생존 신호를 보낸다.
        jogTimer = setInterval(async () => {
          if (!jogHeld) return;
          try { await post('/api/jog/keepalive', {token: controlToken}); }
          catch (_) { await stopJog(); }
        }, 100);
      } catch (error) { jogHeld = false; alert(error.message); }
    }
    function addJogButtons(target, axes) {
      const box = document.querySelector(target);
      axes.forEach(axis => ['+', '-'].forEach(direction => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = axis.toUpperCase() + direction;
        button.setAttribute('aria-label', `${axis.toUpperCase()} ${direction} 방향 JOG`);
        button.onpointerdown = event => { event.preventDefault(); button.setPointerCapture(event.pointerId); startJog(axis, direction); };
        button.onpointerup = stopJog;
        button.onpointercancel = stopJog;
        button.onlostpointercapture = stopJog;
        box.appendChild(button);
      }));
    }
    addJogButtons('#cartesian-jog', ['x', 'y', 'z', 'r']);
    addJogButtons('#joint-jog', ['j1', 'j2', 'j3', 'j4']);
    // Android/iPhone의 길게 누르기 메뉴가 JOG 종료 이벤트를 방해하지 않게 한다.
    document.querySelectorAll('.jog-grid').forEach(box => {
      box.addEventListener('contextmenu', event => event.preventDefault());
      box.addEventListener('selectstart', event => event.preventDefault());
    });
    document.querySelector('#jog-stop').onclick = stopJog;
    document.querySelector('#robot-select').onchange = loadPoints;
    document.querySelector('#save-point').onclick = async () => {
      if (!controlToken) return alert('먼저 이 로봇의 제어권을 획득하세요.');
      const robot = document.querySelector('#robot-select').value;
      const point = document.querySelector('#point-select').value;
      if (!point) return alert('저장할 포인트를 선택하세요.');
      if (!confirm(`${robot}/${point}에 현재 실제 좌표를 저장할까요?`)) return;
      try {
        const result = await post('/api/point/save', {
          token: controlToken, point,
          speed_percent: Number(document.querySelector('#teach-speed').value)
        });
        alert(`${result.robot}/${result.point} 저장 완료`);
        await loadPoints();
      } catch (error) { alert(error.message); }
    };
    document.addEventListener('visibilitychange', () => { if (document.hidden) stopJog(); });
    setInterval(async () => {
      if (!controlToken) return refreshControl();
      try { showControl(await post('/api/control/heartbeat', {token: controlToken})); }
      catch (_) { controlToken = null; sessionStorage.removeItem('dobotControlToken'); refreshControl(); }
    }, 4000);
    const number = value => Number(value).toFixed(2);
    async function refresh() {
      try {
        const response = await fetch('/api/status', {cache: 'no-store'});
        const data = await response.json();
        document.querySelector('#robots').innerHTML = names.map(name => {
          const item = data.robots[name];
          const pose = item.pose;
          return `<section class="card"><h2>${name}</h2>
            <div class="${item.connected ? 'ok' : 'bad'}">
              ${item.connected ? '통신 연결' : '연결 안 됨'}
            </div>
            <div class="pose">${pose ?
              `X ${number(pose.x)} / Y ${number(pose.y)}<br>Z ${number(pose.z)} / R ${number(pose.r)}` :
              '좌표 없음'}</div>
            <div>알람: ${item.alarms?.length ? item.alarms.join(', ') : '없음'}</div>
            ${item.error ? `<div class="bad">${item.error}</div>` : ''}
          </section>`;
        }).join('');
        document.querySelector('#updated').textContent = `마지막 갱신: ${data.read_at}`;
      } catch (error) {
        document.querySelector('#updated').textContent = `상태 읽기 실패: ${error}`;
      }
    }
    refresh();
    setInterval(refresh, 1000);
    refreshControl();
    loadPoints();
  </script>
</body>
</html>"""


def status_snapshot(statuses: dict[str, RobotLiveStatus]) -> dict[str, Any]:
    """상태 객체를 휴대폰이 읽을 수 있는 JSON용 딕셔너리로 바꾼다."""
    from datetime import datetime

    return {
        "read_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "read_only": False,
        "robots": {name: asdict(statuses[name]) for name in ROBOT_NAMES},
    }


class MobileStatusApplication:
    """웹 요청이 동시에 와도 USB 상태 읽기가 겹치지 않도록 관리한다."""

    def __init__(
        self,
        service: IntegratedRobotStatusService,
        teaching_path: Path = DEFAULT_TEACHING_FILE,
    ) -> None:
        self.service = service
        self.teaching_path = teaching_path
        self._lock = threading.Lock()

    def connect(self) -> None:
        self.service.connect_all()

    def read(self) -> dict[str, Any]:
        with self._lock:
            self.service.refresh_all()
            return status_snapshot(self.service.statuses)

    def close(self) -> None:
        self.service.close_all()

    def start_jog(self, robot: str, axis: str, direction: str,
                  speed_percent: float) -> dict[str, Any]:
        """선택 로봇의 JOG를 시작한다. 0.25초 안에 연장 신호가 없으면 멈춘다."""
        with self._lock:
            return self.service.fleet.workers[robot].start_jog(
                axis, direction, 0.25, speed_percent
            )

    def keepalive_jog(self, robot: str) -> dict[str, Any]:
        """휴대폰 버튼이 계속 눌려 있을 때만 JOG 감시시간을 연장한다."""
        with self._lock:
            return self.service.fleet.workers[robot].keepalive_jog(0.25)

    def stop_jog(self, robot: str) -> dict[str, Any]:
        """손가락을 떼거나 화면이 숨겨지면 선택 로봇을 즉시 정지한다."""
        with self._lock:
            return self.service.fleet.workers[robot].stop_jog()

    def points(self, robot: str) -> dict[str, Any]:
        """선택 로봇의 티칭 포인트 이름·좌표·등록 상태를 반환한다."""
        if robot not in ROBOT_NAMES:
            raise ValueError(f"등록되지 않은 로봇입니다: {robot}")
        with self._lock:
            data = load_teaching_data(self.teaching_path)
            points = [
                {"name": name, **point}
                for name, point in data["robots"][robot].items()
            ]
            return {"robot": robot, "points": points}

    def save_point(
        self, robot: str, point_name: str, speed_percent: float
    ) -> dict[str, Any]:
        """알람이 없는 현재 실제 좌표를 선택 포인트에 원자적으로 저장한다."""
        if robot not in ROBOT_NAMES:
            raise ValueError(f"등록되지 않은 로봇입니다: {robot}")
        with self._lock:
            status = self.service.fleet.workers[robot].get_status()
            if status.get("alarms"):
                raise RuntimeError(f"현재 알람으로 티칭 저장 차단: {status['alarms']}")
            data = load_teaching_data(self.teaching_path)
            updated = teach_point(
                data, robot, point_name, status["pose"], speed_percent
            )
            save_teaching_data(updated, self.teaching_path)
            return {
                "robot": robot,
                "point": point_name,
                "pose": updated["robots"][robot][point_name]["pose"],
                "speed_percent": float(speed_percent),
            }


def make_handler(read_status: Callable[[], dict[str, Any]],
                 control: ControlLease | None = None,
                 jog_start: Callable[[str, str, str, float], dict[str, Any]] | None = None,
                 jog_keepalive: Callable[[str], dict[str, Any]] | None = None,
                 jog_stop: Callable[[str], dict[str, Any]] | None = None,
                 read_points: Callable[[str], dict[str, Any]] | None = None,
                 save_point: Callable[[str, str, float], dict[str, Any]] | None = None,
                 ) -> type[BaseHTTPRequestHandler]:
    """상태 읽기 함수가 연결된 HTTP 요청 처리기를 만든다."""

    lease = control or ControlLease()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - HTTP 규격의 메서드 이름이다.
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/status":
                try:
                    body = json.dumps(read_status(), ensure_ascii=False).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                except Exception as exc:
                    body = json.dumps(
                        {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
                    ).encode("utf-8")
                    self._send(503, body, "application/json; charset=utf-8")
                return
            if path == "/api/control":
                self._send_json(200, lease.snapshot())
                return
            if path == "/api/points" and read_points is not None:
                try:
                    robot = parse_qs(urlparse(self.path).query).get("robot", [""])[0]
                    self._send_json(200, read_points(robot))
                except (TypeError, ValueError) as exc:
                    self._send_json(400, {"error": str(exc)})
                except Exception as exc:
                    self._send_json(503, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self._send(404, b"Not Found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802 - HTTP 규격의 메서드 이름이다.
            path = urlparse(self.path).path
            actions = {
                "/api/control/acquire": lambda data: lease.acquire(str(data.get("client_id", "")), str(data.get("robot", ""))),
                "/api/control/heartbeat": lambda data: lease.heartbeat(str(data.get("token", ""))),
                "/api/control/release": lambda data: lease.release(str(data.get("token", ""))),
            }
            if jog_start and jog_keepalive and jog_stop:
                actions.update({
                    "/api/jog/start": lambda data: jog_start(
                        lease.robot_for(str(data.get("token", ""))),
                        str(data.get("axis", "")), str(data.get("direction", "")),
                        float(data.get("speed_percent", 0)),
                    ),
                    "/api/jog/keepalive": lambda data: jog_keepalive(
                        lease.robot_for(str(data.get("token", "")))
                    ),
                    "/api/jog/stop": lambda data: jog_stop(
                        lease.robot_for(str(data.get("token", "")))
                    ),
                })
            if save_point is not None:
                actions["/api/point/save"] = lambda data: save_point(
                    lease.robot_for(str(data.get("token", ""))),
                    str(data.get("point", "")),
                    float(data.get("speed_percent", 0)),
                )
            if path not in actions:
                self._send(404, b"Not Found", "text/plain; charset=utf-8")
                return
            try:
                if "application/json" not in self.headers.get("Content-Type", ""):
                    raise ValueError("JSON 요청만 허용됩니다.")
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4096:
                    raise ValueError("요청 크기가 올바르지 않습니다.")
                data = json.loads(self.rfile.read(length))
                self._send_json(200, actions[path](data))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            except (RuntimeError, PermissionError) as exc:
                self._send_json(409, {"error": str(exc)})
            except Exception as exc:
                # SDK 알람, Worker 시간 초과 등 예상하지 못한 장비 오류가 나더라도
                # HTTP 연결을 갑자기 닫지 않고 휴대폰에 원인을 문자열로 알려준다.
                self._send_json(
                    503,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )

        def _send_json(self, code: int, value: dict[str, Any]) -> None:
            self._send(code, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # 휴대폰의 1초 갱신 로그가 화면을 가득 채우지 않도록 기본 로그를 끈다.
            return

    return Handler


def local_ipv4() -> str:
    """휴대폰에 안내할 제어 PC의 일반적인 사설 IPv4 주소를 찾는다."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect는 실제 데이터를 보내지 않고 사용할 네트워크 주소만 선택한다.
        probe.connect(("192.0.2.1", 9))
        return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dobot 모바일 상태 조회 서버")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--lan", action="store_true",
        help="같은 Wi-Fi/LAN의 휴대폰에서도 접속 허용",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    app = MobileStatusApplication(IntegratedRobotStatusService())
    app.connect()
    server = ThreadingHTTPServer(
        (host, args.port),
        make_handler(
            app.read, ControlLease(), app.start_jog, app.keepalive_jog, app.stop_jog
            , app.points, app.save_point
        ),
    )
    address = local_ipv4() if args.lan else "127.0.0.1"
    print("모바일 Dobot 티칭 서버를 시작했습니다.")
    print(f"접속 주소: http://{address}:{args.port}")
    print("종료: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("서버를 종료합니다.")
    finally:
        server.server_close()
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
