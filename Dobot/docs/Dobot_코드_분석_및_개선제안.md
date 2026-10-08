# Dobot 프로젝트 코드 분석 및 개선 제안

대상: `DobotControl_Main`, `DobotControl_Test` (2026-09-10 기준 코드)

전체적으로 `DobotControl_Main`은 안전장치(속도·이동거리 제한, JOG watchdog, 프로세스 격리, 원자적 파일 저장, 입력 검증 dataclass 등)가 매우 꼼꼼하게 설계되어 있습니다. 반면 `DobotControl_Test`는 초기 프로토타입 성격이 강하고, Main에서 이미 해결된 안전 문제들이 그대로 남아 있습니다. 아래는 실제로 발견한 버그와 개선 여지를 우선순위별로 정리한 것입니다.

---

## 1. 실제 버그 (동작 오류로 이어짐)

### 1-1. `DobotControl_Test/robot.py` — `is_connected` 속성이 초기화되지 않음 (크래시 위험)
`DobotRobot.__init__`에는 `self.is_connected = True`를 설정하는 코드가 없습니다. 그런데 `disconnect()`의 첫 줄은 다음과 같습니다.

```python
def disconnect(self):
    if not self.is_connected:   # <-- 존재하지 않는 속성 참조
        return
```

`DobotRobot` 인스턴스를 만든 뒤 `disconnect()`를 처음 호출하면 `AttributeError: 'DobotRobot' object has no attribute 'is_connected'`가 발생합니다. 문제는 이 오류가 나면 `dType.DisconnectDobot(self.api)`가 아예 실행되지 않아 **COM 포트가 계속 점유된 상태로 남는다**는 점입니다. `main.py`의 예외 처리 블록, `gui.py`의 `_finish_disconnect()`, 그리고 `gripper_test.py`/`suction_test.py`의 `finally: robot.disconnect()`가 모두 이 경로를 타므로, 프로그램이 예외로 종료될 때마다 포트가 풀리지 않을 가능성이 높습니다.

**수정 제안**: `__init__` 끝에 `self.is_connected = True`를 추가하고, 연결 실패 시 예외를 던지기 전에는 설정하지 않도록 합니다.

### 1-2. `DobotControl_Main/dobot_ptp.py` — 오류 메시지가 실제 허용 범위와 다름
```python
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 120.0
...
if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
    raise ValueError("PTP 도착 대기시간은 1~15초여야 합니다.")
```
상수는 1~120초인데 오류 메시지는 "1~15초"로 되어 있습니다. 예전 버전에서 남은 문구로 보입니다. `robot_control/robot_motion_controller.py`와 `teaching/teaching_gui.py`는 실제로 90초 타임아웃을 사용하므로 이 메시지를 본 사용자는 "90초는 왜 되지?"라며 혼란스러울 수 있고, 반대로 실제 한도(120초)를 15초로 오인해 불필요하게 값을 낮출 수도 있습니다. `tests/test_dobot_ptp.py`에도 이 경계값(120초 초과)을 검증하는 테스트가 없어서 지금까지 잡히지 않은 것으로 보입니다.

**수정 제안**: 메시지를 `f"PTP 도착 대기시간은 {MIN_TIMEOUT:.0f}~{MAX_TIMEOUT:.0f}초여야 합니다."`처럼 상수를 참조하도록 바꾸고, 경계값 테스트를 하나 추가하면 이런 종류의 드리프트를 앞으로 방지할 수 있습니다.

### 1-3. `DobotControl_Test/gui.py` — 포트 하드코딩 불일치
`main.py`는 `DobotRobot(port="COM5", ...)`로 연결하는데, `gui.py`의 `connect_robot()`(로봇 없이 GUI만 실행되거나 "재연결" 버튼을 눌렀을 때 호출됨)은 다음처럼 포트가 다르게 하드코딩되어 있습니다.
```python
self.robot = DobotRobot(port="COM3", baudrate=115200)
```
재연결 시 실제로 연결하려는 로봇과 다른 COM 포트를 시도하게 되어, 포트가 COM5인 환경에서는 "재연결" 버튼이 항상 실패하거나(최악의 경우 다른 장치가 COM3에 물려 있다면 엉뚱한 장치에 연결) 예기치 않은 동작을 일으킵니다.

**수정 제안**: `Main` 프로젝트처럼 포트를 설정 파일이나 생성자 인자로 넘겨 GUI가 하드코딩된 값을 갖지 않도록 하는 것이 근본적인 해결책입니다.

### 1-4. `DobotControl_Test/gripper_test.py`, `gripper_immediate_test.py` — 생성 실패 시 `NameError`로 원인 오류가 가려짐
```python
robot = DobotRobot()
try:
    ...
finally:
    robot.disconnect()
```
`DobotRobot()` 생성자 자체가 실패하면(포트 연결 실패 등) `robot` 변수가 만들어지지 않은 채 `finally`에서 `robot.disconnect()`를 호출하려다 `NameError`가 발생합니다. 진짜 원인(연결 실패)이 `NameError`에 가려져 디버깅이 어려워집니다. `suction_test.py`는 `robot = None`으로 먼저 초기화하고 `if robot is not None:`으로 방어하고 있으니 같은 패턴을 적용하면 됩니다.

---

## 2. 설계·안전 관점 개선 제안

### 2-1. `DobotControl_Test`에는 JOG 정지 실패에 대한 안전망(watchdog)이 없음
`Main`의 `dobot_worker.py`는 JOG 시작 후 정지 명령이 오지 않아도 0.25초 안에 자동으로 멈추는 watchdog(`jog_deadline`)을 갖고 있습니다. 반면 `Test`의 `gui.py`는 버튼의 `<ButtonRelease-1>`/`<Leave>` 이벤트에만 의존합니다. 창이 포커스를 잃거나(Alt-Tab), 이벤트가 드물게 유실되는 경우 로봇이 정지 명령 없이 계속 움직일 수 있습니다. 이미 `Main`에 구현되어 검증된 watchdog 패턴을 `Test`에도 이식하거나, `Test` 프로젝트를 더 이상 실제 로봇 구동에 쓰지 않도록 정리하는 것을 권장합니다.

### 2-2. `DobotControl_Test/robot.py`의 `home()`에 좌표가 하드코딩됨
```python
home_x = 213.0  #Dobot_1 : 200.0, Dobot_2 :163.0
home_y = 3.0    #Dobot_1 : -20.0, Dobot_2 : -122.0
```
로봇마다 다른 HOME 좌표를 주석으로만 남겨두고 실제 코드는 하나의 좌표만 사용합니다. 다른 로봇에 이 코드를 재사용하면 잘못된 HOME 위치로 이동할 위험이 있습니다. `Main`은 이미 `teaching_points.json` 기반으로 로봇별 좌표를 분리했으니, `Test`를 계속 쓴다면 같은 방식으로 옮기는 것이 안전합니다.

### 2-3. `DobotWorker.move_ptp`의 타임아웃 여유값이 상한 근처에서 어긋날 수 있음 (`dobot_worker.py`)
```python
return self.request(CommandName.MOVE_PTP, arrival_timeout + 2.0, payload)
```
`PTPRequest`는 `timeout`(=`arrival_timeout`)을 최대 120초까지 허용하지만, `request()`가 호출하는 `validate_timeout()`은 최대 120초까지만 허용합니다. `arrival_timeout`이 118~120초 사이면 `arrival_timeout + 2.0`이 120을 넘어 `ValueError`가 발생합니다. 지금은 실제 호출부(`robot_control/robot_motion_controller.py`, `teaching/teaching_gui.py`)가 90초만 쓰므로 실무에서 문제가 되지는 않지만, 이후 타임아웃 값을 늘릴 때 함정이 될 수 있는 경계 불일치입니다. `validate_timeout`의 상한을 조금 늘리거나, `move_ptp`가 여유값을 더한 뒤 클램프하도록 명시하면 좋습니다.

### 2-4. 그리퍼/흡착컵 미장착 오류 가능성
`robot_control/dobot_end_effector.py`의 `EndEffectorRequest`는 `tool_type`이 로봇에 실제로 장착된 종류(`robots.json`의 `end_effector`)와 일치하는지 검사하지 않습니다. `Worker.set_end_effector`를 호출하는 코드(`teaching/teaching_gui.py`, `robot_control/robot_motion_controller.py`)는 항상 `self.config["robots"][...]["end_effector"]`를 조회해서 쓰므로 실사용 경로에서는 문제가 없지만, 이 계층을 우회해서 직접 `worker.set_end_effector("suction", "on")`처럼 실제 장착과 다른 tool_type을 호출하면 SDK가 예기치 않게 응답할 수 있습니다. `Worker` 계층에서 로봇 설정의 `end_effector`와 비교해 한 번 더 막아주면 더 안전합니다.

---

## 3. 유지보수/코드 정리 제안

- **`DobotControl_Test`의 위치**: README를 보면 `Main`이 `Test` 폴더의 SDK 파일(`DobotDllType.py`, `DobotDll.dll`)을 그대로 가져다 쓰는 구조입니다. 즉 `Test`는 이제 "SDK 보관소 + 실험용 스크립트" 역할만 남은 것으로 보입니다. `Test`의 `robot.py`/`gui.py`는 위에서 언급한 안전 문제들을 안고 있으므로, 실제 생산 라인에서는 쓰지 않는 것이 좋고, 필요하다면 SDK만 별도 폴더(`sdk/`)로 분리해 `Main`과 공유하고 `Test`의 나머지 스크립트는 정리하는 것을 권장합니다.
- **`test.py`(Test 폴더)**: 로봇과 무관한 `input()` 연습 스크립트로 보입니다. 저장소에서 제거해도 될 것 같습니다.
- **`robot.py` 상단의 모듈 임포트 시 디버그 출력**:
  ```python
  print("실제로 불러온 DobotDllType 경로:")
  print(dType.__file__)
  print("SetPTPCmd 존재 여부:")
  print(hasattr(dType, "SetPTPCmd"))
  ```
  이 코드는 `robot.py`를 **import만 해도** 실행됩니다(`gui.py`, `main.py` 등 어디서든). 디버깅용으로 넣어둔 것이라면 `if __name__ == "__main__":` 안으로 옮기거나 로깅 레벨로 감싸는 것이 좋습니다.
- **`__pycache__` 폴더**: 두 프로젝트 모두 `__pycache__`가 소스와 같은 폴더에 남아 있습니다. 실제 운용에는 문제없지만 백업/버전 비교 시 불필요한 잡음이 됩니다. `.gitignore` 또는 백업 스크립트에서 제외 목록에 추가하는 것을 권장합니다(현재 저장소가 git으로 관리되고 있는지 확인이 필요합니다 — 버전관리가 아직 없다면 이 정도 규모의 안전-크리티컬 코드베이스는 git 도입을 강력히 추천합니다).
- **로그 폴더 정리**: `DobotControl_Main/logs`에 연결 점검 로그가 계속 쌓이고 있습니다(9월 초부터 다수). 자동 정리(예: 30일 이상 로그 삭제) 스크립트를 하나 추가하면 디스크 관리에 도움이 됩니다.

---

## 4. 잘 되어 있는 부분 (참고용)

- `dobot_worker.py`의 프로세스 격리 설계(로봇 1대당 독립 프로세스, SDK 전역 상태 충돌 회피)와 `dobot_fleet.py`의 로봇별 `try/except` 격리는 4대 동시 운용 환경에 적합한 견고한 구조입니다.
- `robot_control/dobot_config.py`/`teaching/teaching_points.py`의 "임시 파일 작성 후 교체(`.tmp` → `replace`)" 방식은 설정 파일 저장 중 정전/충돌에도 파일이 손상되지 않도록 하는 좋은 습관입니다.
- `JogRequest`/`PTPRequest` 같은 dataclass 기반 입력 검증과, 이동 전 알람 확인 → 이동 → 도착 확인(3회 연속) 패턴은 산업용 로봇 제어에 필요한 안전 검증을 잘 반영하고 있습니다.
- 각 스크립트가 `--execute` 플래그 없이는 DRY-RUN만 하도록 만든 CLI 설계는 현장 오조작을 막는 데 실질적으로 도움이 됩니다.

---

## 요약 우선순위

| 우선순위 | 항목 | 파일 |
|---|---|---|
| 높음 | `is_connected` 미초기화로 disconnect 시 크래시·포트 미해제 | `DobotControl_Test/robot.py` |
| 높음 | 재연결 시 COM 포트 하드코딩 불일치(COM3 vs COM5) | `DobotControl_Test/gui.py` |
| 중간 | PTP 타임아웃 오류 메시지가 실제 범위(120초)와 다름(15초로 표시) | `DobotControl_Main/dobot_ptp.py` |
| 중간 | 생성자 실패 시 `NameError`로 원인 오류가 가려짐 | `DobotControl_Test/gripper_test.py`, `gripper_immediate_test.py` |
| 중간 | JOG watchdog 부재 (버튼 이벤트 유실 시 무한 이동 가능) | `DobotControl_Test/gui.py` |
| 낮음 | HOME 좌표 하드코딩 | `DobotControl_Test/robot.py` |
| 낮음 | move_ptp 타임아웃 경계 불일치(118~120초 구간) | `DobotControl_Main/dobot_worker.py` |
| 낮음 | import 시점 디버그 출력, 미사용 test.py, `__pycache__` 정리 | `DobotControl_Test` 전반 |
