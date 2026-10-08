# 공개용 Dobot 코드 안내

이 복사본은 포트폴리오 공개용입니다. 실제 현장 좌표는 제거되어 모든 티칭 포인트가 미티칭 상태이고, 시리얼번호와 OPC-UA 주소는 예시값입니다. 아래 원본 개발 문서의 현장 적용 기록은 개발 당시 설명이며 공개본 기본 설정과 구분해야 합니다.

제조사 SDK와 DLL은 재배포 권한을 확인하지 못해 포함하지 않습니다. 실제 실행에는 공식 Dobot SDK 및 드라이버를 별도 준비하여 `sdk/`에 배치해야 합니다. `DobotDllType.py`와 DLL 파일명은 아래 개발 문서를 참고하세요.

# DobotControl Main

## 팀원용 빠른 시작

이 프로젝트는 PC 한 대에서 Dobot Magician 4대를 각각 독립 Worker 프로세스로
제어하고, KEPServerEX OPC-UA를 통해 Mitsubishi PLC와 START 및 상태 신호를
교환합니다. 처음 실행하는 팀원은 아래 순서를 지켜 주세요.

채용 포트폴리오용 프로젝트 개요와 담당 역할, 핵심 설계 내용은
[`docs/PORTFOLIO_PROJECT_OVERVIEW.md`](docs/PORTFOLIO_PROJECT_OVERVIEW.md)에서 확인할 수 있습니다.

```powershell
cd ./Dobot
python -m pip install -r requirements-opcua.txt
python -m unittest discover -s tests -v
python -m manual_tests.dobot_connection_test
python .\dobot_port_recovery.py
python -m manual_tests.dobot_identity_test
python -m manual_tests.multi_dobot_status_test
python .\integrated_gui.py
```

`tests`는 실제 장비 없이 실행됩니다. `manual_tests`는 실제 USB 장비를 사용하므로
한 번에 한 프로그램만 실행하고, `--execute`가 붙은 명령은 주변 간섭과 비상정지를
확인한 경우에만 실행합니다. PC 또는 로봇 전원을 다시 켠 뒤 COM 번호가 바뀌면
반드시 `dobot_connection_test → dobot_port_recovery → identity/status` 순서로 확인합니다.

전체 실행 흐름은 다음과 같습니다.

```text
PLC/KEPServerEX
  │ START 입력 / RUNNING·FINISH·ERROR·READY 및 좌표 32개 출력
  ▼
communication/automatic_operation.py   ← 수동·자동 공용 OPC-UA 스레드
  ▼
robot_control/live_process_manager.py   ← 신호 상태와 실제 공정 결합
  ▼
robot_control/process_state_machine.py  ← 로봇별 기동 조건과 상태 전이
  ▼
robot_control/robot_motion_controller.py← 티칭 이름을 안전 이동 순서로 변환
  ▼
robot_control/dobot_worker.py           ← 로봇별 별도 프로세스와 제조사 SDK
  ▼
Dobot Magician 1~4
```

팀원이 먼저 읽을 핵심 파일은 다음과 같습니다.

| 파일 | 역할 |
|---|---|
| `config/robots.json` | 로봇 이름, 현재 COM, 시리얼번호, 공구, 필수 포인트 목록 |
| `config/teaching_points.json` | GUI에서 저장한 실제 X/Y/Z/R과 포인트별 속도 |
| `integrated_gui/integrated_gui.py` | 4대 연결, 수동·자동 운전 및 PLC 신호 화면 |
| `communication/robot_opcua_tags.py` | KEPServerEX 실제 NodeId의 단일 관리 위치 |
| `communication/robot_position_opc.py` | SCADA용 X/Y/Z/R/J1~J4 좌표 32개 전송 |
| `robot_control/live_process_manager.py` | READY/RUNNING/FINISH/ERROR 결정 위치 |
| `robot_control/robot_motion_controller.py` | HOME, 픽업, 조립·반환, ready 복귀 순서 |
| `manual_tests/` | 실제 장비를 단계별로 확인하는 작업자용 검사 |
| `tests/` | 장비 없이 회귀 오류를 찾는 자동 테스트 |

루트의 `integrated_gui.py`, `teaching_gui.py`, `dobot_config.py`,
`dobot_port_recovery.py`, `kepserver_opcua.py`는 기존 실행 명령을 유지하기 위한
짧은 진입점입니다. 실제 기능을 수정할 때는 각 패키지 안의 파일을 수정합니다.

## 프로젝트 폴더 구조

기능별 코드는 다음 Python 패키지로 구분한다. 루트의 짧은 실행 파일은 기존
명령을 유지하기 위한 진입점이며, 실제 기능은 각 패키지 안에 있다.

```text
DobotControl_Main/
├─ sdk/              제조사 Python 래퍼, 제어 DLL 및 의존 DLL
├─ robot_control/    실제 Dobot 연결, Worker, JOG, PTP 및 공정 제어
├─ teaching/         PC·모바일 티칭 GUI와 티칭 포인트 관리
├─ integrated_gui/   Dobot 4대 통합 상태 및 수동·자동 운전 화면
├─ communication/    PLC, KEPServerEX 및 OPC-UA 통신
├─ config/           로봇 등록 정보와 티칭 좌표
├─ tests/            실제 장비가 필요 없는 자동 테스트
├─ manual_tests/     실제 장비를 사용하는 단계별 시험
├─ logs/             USB 연결 검사 결과
└─ docs/             OPC-UA 및 코드 분석 문서
```

자주 사용하는 실행 명령은 폴더 정리 전과 동일하다.

```powershell
python .\teaching_gui.py
python .\integrated_gui.py
python .\dobot_port_recovery.py
python .\kepserver_opcua.py --help
```

내부 코드를 새로 작성할 때는 루트 실행 파일이 아니라 `robot_control`,
`teaching`, `integrated_gui`, `communication` 패키지에서 가져온다.

## 수동 / 자동 운전 모드

티칭 GUI 하단의 **6. 실제 1사이클 이동 포인트 순서**에서 선택한 로봇의
목적지별 이동 경로를 확인할 수 있습니다. Dobot_2는 assembly_1/assembly_2,
Dobot_4는 제품·적층 목적지를 선택할 수 있습니다. 각 포인트 옆에는 저장 속도에
수동 검증 사이클 최대 10% 제한을 적용한 요청 속도가 표시됩니다. PLC START로
실행되는 자동모드는 저장 속도를 그대로 따르되 최대 30%로 제한합니다.

`python integrated_gui.py`로 실행합니다. 기본값은 수동 모드입니다.

- **수동 모드**: 실제 4대 연결이 끝나면 OPC-UA 연결도 시작되어 PLC START를 읽고
  RUNNING/FINISH/ERROR/READY를 계속 기록합니다. PLC START는 GUI에 표시만 하며
  로봇을 자동 기동하지 않습니다. 실제 기동은 GUI의 수동 버튼으로만 실행합니다.
  Dobot_2는 수동 기동 전에 `첫 번째 조립` 또는 `두 번째 조립`을 선택해 해당
  조립 위치의 1사이클을 실행합니다. 자동모드는 PLC의 목표·완료 카운트로
  조립 위치를 결정하므로 이 화면 선택값을 사용하지 않습니다.
  통합 GUI의 `선택 로봇 실제 1사이클 실행`은 이동이 시작되면 RUNNING을 켜고
  사이클 완료 시 내립니다. 별도 티칭 GUI의 개별 PTP/JOG 이동은 이 PLC 공정
  신호에 포함되지 않습니다.
- OPC-UA가 끊겼거나 `BadShutdown`이 발생하면 KEPServerEX 상태를 확인한 뒤
  `OPC-UA 연결/재연결`을 누릅니다.
  이 두 버튼은 Dobot의 USB/COM 연결을 해제하지 않습니다. 재연결 전 작업자가
  READY를 승인한 대기 로봇만 PLC READY를 다시 켜며, 자동모드는 작업자가 다시
  선택해야 합니다. 통신이 끊긴 동안의 FINISH는 재전송하지 않으므로 PLC 완료
  카운트를 확인하세요.
- **자동 모드 (PLC 기동)**: 실제 4대 연결 후 각 로봇의 작업자 READY 승인을 마친 대기 상태에서 선택합니다.
- 기계적 HOME을 실행하지 않아도 통합 GUI에서 로봇을 선택하고
  `선택 로봇 PLC READY 승인`을 누르면 해당 로봇의 PLC READY를 켭니다.
  이 버튼은 위치·알람을 자동 검사하거나 로봇을 이동시키지 않습니다.
  현재 위치와 주변 간섭 여부는 작업자가 확인해야 합니다. 운전 중·오류 상태에서는
  승인할 수 없으며, 사이클 완료 후 READY는 기존처럼 자동으로 켜집니다.
  PLC START를 약 250ms 대기 간격으로 반복 조회합니다(통신 시간만큼 실제 주기는 늘어납니다).
  START는 RUNNING ON을 확인할 때까지 유지하고, 확인 후 OFF로 내리세요.
  짧은 펄스는 놓칠 수 있습니다. 실행 중 다음 작업을 예약할 때도 START OFF가
  읽힌 뒤 새 ON을 보내야 합니다. 예약된 작업은 FINISH OFF·READY ON 후 실행됩니다.
- 자동 진입 시 이미 ON인 START는 실행하지 않습니다. OFF를 관측한 뒤 새 ON부터 기동합니다.
- 각 로봇은 독립 실행합니다. 실행 중 들어온 새 START 상승 에지는 로봇별로
  다음 작업 한 건만 예약하고, 그 이상의 요청은 무시합니다. 수동 전환·통신 종료·
  해당 로봇 오류 시 예약은 취소됩니다. 기존 PLC 태그에는 예약 접수 확인 신호가 없습니다.
- Dobot_1은 재료를 집어 `pickup_approach`로 상승한 뒤 `ready`를 경유하여
  `assembly_1_approach`로 이동합니다. 조립 완료 후에도 기존과 같이 `ready`로 복귀합니다.
- Dobot_2는 START 수락 시 `ROBOT2_TARGET_COUNT`(1 또는 2)와
  `ROBOT2_FINISH_COUNT`(0 이상, 목표 미만)를 읽습니다. 완료 0이면 assembly_1,
  완료 1이면 assembly_2를 한 번 실행합니다. `ROBOT_Program_NO_(2)`는 기동 조건에
  사용하지 않습니다. 횟수 증가/초기화는 PLC가 담당합니다. 목표 2·완료 0인
  첫 조립 중 추가 START는 예약하지 않습니다. 첫 FINISH를 PLC가 카운트한 뒤
  목표 2·완료 1에서 새 START를 보내야 두 번째 조립이 시작됩니다. 마지막 조립
  중 추가 START만 다음 제품용으로 한 건 예약합니다. 이전 FINISH 펄스가 끝난 뒤
  PLC의 목표/완료 `0/0` 초기화를 Python이 관측하고, 새 제품의 목표 1 또는 2·
  완료 0이 30초 안에 준비되어야 예약 공정이 시작됩니다. `0/0` 상태가 OPC-UA
  조회 사이에 지나가면 예약은 실행되지 않으므로 충분히 유지해야 합니다.
- Dobot_2의 첫 번째 조립은 `pickup_approach → safe_point →
  assembly_2_approach → assembly_1_approach → assembly_1` 순서로 진입합니다.
  두 번째 조립은 `pickup_approach → safe_point → assembly_2_approach →
  assembly_2`로 진입합니다. 첫 번째 조립 후에는 복귀 전용
  `assembly_1_approach2 → safe_point → ready`를 사용합니다. 이 새 포인트는
  티칭 GUI에서 실제 좌표를 저장하기 전까지 첫 번째 조립 사이클을 차단합니다.
  두 번째 조립 후에는 기존처럼 `assembly_2_approach → safe_point → ready`로
  복귀합니다.
- Dobot_4는 START 상승 순간 `ns=2;s=M.PLC.VISION_I/O.VISION4_CLASS_RESULT`를
  한 번 읽어 경로를 고정합니다. 코드 2/3/4/5/6은 각각 Lamp_a/Lamp_b/
  Seat_a/Seat_b/Main이며, 그 밖의 값이면 이동하지 않고 ERROR를 출력합니다.
- Dobot_3은 `ready → pickup_approach_1 → pickup` 순서로 흡착하러 들어가고,
  흡착 후에는 `pickup_approach_1 → pickup_approach_2`로 빠져나옵니다.
- Dobot_4의 Lamp_a·Lamp_b·Seat_a·Seat_b 공급은 `jig_pickup_approach`로
  픽업 위치에서 나온 뒤 `ready`를 거쳐 해당 반환 위치 상부로 이동합니다.
  Main 경로는 기존 순서를 유지합니다.
- Dobot_4 차량 하부 창고는 최대 3개 적층 방식입니다. 자동 모드 전에 GUI에서
  실제 재고 0~3을 입력하고 `재고 확정`을 눌러야 합니다. 비전 코드 6으로 차량
  하부를 반환할 때 현재 재고 0/1/2에 따라 각각 `storage_base_1st/2nd/3rd`에
  놓으며, `ready` 복귀까지 정상 완료된 후에만 재고를 1 증가시킵니다. 3개이면
  `가득 참` 오류로 이동 전에 차단합니다.
- 자동운전 중에도 Dobot_4가 대기 상태라면 작업자가 실제 창고에 차량 하부를
  수동으로 넣거나 꺼낸 뒤 GUI의 수량을 0~3으로 수정하고 `재고 확정`을 누를 수
  있습니다. Dobot_4 입고·출고 사이클 실행 중에는 수량 변경을 차단합니다.
- `storage_base_1st/2nd/3rd`와 각 층 전용 상부점
  `storage_base_1st_approach`, `storage_base_2nd_approach`,
  `storage_base_3rd_approach`를 티칭 GUI에서 실제 높이로 티칭해야 합니다.
  기존 `storage_base_approach` 좌표는 `storage_base_1st_approach`로 이전했으며,
  새 2층·3층 상부점은 실제 좌표를 티칭하기 전까지 해당 사이클을 차단합니다.
- Dobot_4의 `ROBOT_START(B)_(4)` 상승 에지가 들어오면 현재 재고의 최상단
  `storage_base_3rd/2nd/1st`를 선택하여 `ready → 선택 층 전용 approach →
  선택 적층점 → 같은 층 approach → main_conveyor_approach →
  main_conveyor → main_conveyor_approach → ready` 순서로 차량 하부를 공급합니다.
  정상 완료 후에만 재고를 1 감소시키고 `ROBOT_FINISH(B)_(4)`를 약 1초간
  출력합니다. 재고가 0이면 이동 전에 ERROR로 차단합니다.
- `ROBOT_START_(4)` 입고 사이클과 `ROBOT_START(B)_(4)` 출고 사이클은 같은
  Dobot_4를 사용하므로 동시에 실행하지 않습니다. 실행 중 들어온 다음 START는
  종류와 관계없이 한 건만 예약합니다.
- RUNNING/FINISH/READY/ERROR를 PLC에 기록합니다. FINISH는 사이클 완료 후
  OPC-UA에 ON을 기록한 때부터 약 1초간 유지한 뒤 자동으로 OFF로 전환합니다.
  START의 OFF 여부와 무관하며, PLC는 FINISH 상승 에지를 한 번 카운트해야 합니다.
- 수동 전환 시 새 기동을 차단하고 진행 공정 종료 및 마지막 상태 전송 후 수동으로 돌아옵니다.
  즉시 정지는 별도 GUI 비상정지 버튼입니다. 일시정지 중이면 재개 또는 정지로 공정을 끝내야 합니다.
- 통신/품질/카운터 오류 시 자동 모드를 종료합니다. 진행 중 공정은 완료를 기다립니다.
  통신이 끊기면 PLC로 오류 상태를 전송할 수 없으므로 PLC 측 통신 감시도 필요합니다.
- 자동 감시 중 수동 기동/초기화/연결 변경/입력 변경은 비활성화됩니다.
- 수동·자동은 같은 OPC-UA 연결을 공유합니다. 자동 모드 전환은 연결을 새로 만들지
  않고 PLC START의 실제 기동 허용 여부만 바꿉니다.
- 각 로봇 카드에는 PLC의 START 입력과 Python이 보내는 RUNNING/FINISH/ERROR/READY가
  ON/OFF로 표시됩니다. 작업자가 READY를 승인하면 수동 모드에서도 READY를
  KEPServerEX에 계속 기록합니다.
- 이동 없는 작업 초기화 뒤에는 READY를 OFF로 유지합니다. 작업자가 다시 승인하면
  HOME 이동 없이 READY가 ON 됩니다.
- 실제 4대 연결이 성공하면 SCADA 좌표 전송도 자동으로 시작됩니다. START·상태·좌표는
  KEPServerEX OPC-UA 연결 하나를 공유합니다. 기존 Worker가
  읽은 실제 X/Y/Z/R/J1~J4만 공유하므로 추가 COM 연결이나 이동 명령은 없습니다.
  KEPServerEX에는 `M.PLC.ROBOT_I/O.Coord.ROBOT1_X`부터 `ROBOT4_J4`까지 총
  32개 Float 태그를 약 1초 간격으로 기록합니다.
- 좌표가 3초 이상 갱신되지 않은 로봇은 오래된 값을 다시 쓰지 않고 해당 로봇만
  이번 전송에서 제외합니다. 통신 오류가 나도 로봇 제어는 중단하지 않고 재접속합니다.

SCADA와 같은 좌표 태그를 PowerShell에서 읽기 전용으로 확인하려면 실행합니다.

```powershell
python -m manual_tests.opcua_robot_positions
```

검증: `python -m unittest tests.test_automatic_operation tests.test_gui_operation_modes -v`
개발 중 실제 로봇 기동 및 PLC 상태 쓰기는 수행하지 않았습니다.

### PLC 신호 상태표

| 상황 | START | RUNNING | FINISH | ERROR | READY |
|---|---:|---:|---:|---:|---:|
| 연결 직후, HOME 전 | PLC 입력 | OFF | OFF | OFF | OFF |
| HOME 및 ready 이동 중 | PLC 입력 | ON | OFF | OFF | OFF |
| ready 위치에서 대기 | PLC 입력 | OFF | OFF | OFF | ON |
| 공정 실행 중 | PLC 입력 | ON | OFF | OFF | OFF |
| 공정 완료 후 ready 복귀, 약 1초 | PLC 입력 | OFF | ON | OFF | ON |
| FINISH 펄스 종료 후 | PLC 입력 | OFF | OFF | OFF | ON |
| 오류·비상정지 | PLC 입력 | OFF | OFF | ON | OFF |
| 이동 없는 작업 초기화 | PLC 입력 | OFF | OFF | OFF | OFF |

START는 PLC가 Python으로 보내는 입력이고 나머지 네 신호는 Python이 PLC로 보내는
출력입니다. 수동 모드에서는 START를 읽고 GUI에만 표시하며 자동 기동하지 않습니다.
자동 모드로 전환할 때 이미 ON인 START도 실행하지 않고, OFF를 확인한 다음 새 ON부터
한 번만 실행합니다.

### KEPServerEX 읽기 전용 확인

```powershell
python .\kepserver_opcua.py check
python .\kepserver_opcua.py browse --depth 3
python .\kepserver_opcua.py groups "ns=2;s=M.PLC.ROBOT_I/O"
python -m manual_tests.opcua_start_status
```

4대 READY만 확인하려면 다음 명령을 사용합니다.

```powershell
python .\kepserver_opcua.py read `
  "ns=2;s=M.PLC.ROBOT_I/O.ROBOT_READY_(1)" `
  "ns=2;s=M.PLC.ROBOT_I/O.ROBOT_READY_(2)" `
  "ns=2;s=M.PLC.ROBOT_I/O.ROBOT_READY_(3)" `
  "ns=2;s=M.PLC.ROBOT_I/O.ROBOT_READY_(4)"
```

기본 서버 주소는 `opc.tcp://127.0.0.1:49320`입니다. 실제 태그 이름은
`communication/robot_opcua_tags.py`에서만 관리하며 다른 파일에 중복 작성하지 않습니다.

## 검사 코드 폴더 구분

- `tests/`: 실제 Dobot 없이 가짜 Worker로 실행하는 자동 단위 테스트
- `manual_tests/`: 사용자가 통신·상태·JOG·HOME·공정 경로를 개별 점검하는 스크립트

`manual_tests`는 MAIN 폴더에서 `python -m manual_tests.파일이름` 형식으로 실행합니다.
파일에 `--execute`가 있는 경우 이 옵션을 생략하면 실제 이동을 하지 않습니다.

## USB 통신 점검

### 수동 PTP 속도 조절

티칭 GUI의 **5. 티칭 포인트 저장 및 이동**에서 **수동 PTP 속도(5~100%)**를
입력하고 **선택 포인트로 이동**을 누릅니다. 기본값은 5%이며 0.1% 단위로 조절합니다. 확인 창에 표시된
속도로 이동합니다. 입력값 변경은 다음 이동부터 적용됩니다.
이 속도는 JOG 속도, 포인트에 저장된 속도, 자동운전 속도와 독립적입니다.
범위를 벗어나거나 숫자가 아닌 값은 이동 전에 차단합니다.
장비에서 확인된 PTP 지원 범위인 5~100%를 common ratio에 그대로 적용하며,
분할 이동이나 인위적인 정지 시간은 사용하지 않습니다. 관절·직교 기준속도는
현재 장비 값을 유지합니다. 속도 설정과 이동을 같은 큐에 순서대로 등록해 실행합니다.
실행 전 common 조회값과의 비교는 하지 않으며, 완료 화면에는 요청 속도를 표시합니다.
`7`과 `7.0`은 모두 같은 7% 요청입니다.

### 연결 진단

`manual_tests/dobot_connection_test.py`는 로봇을 움직이지 않고 Dobot USB 통신만 확인합니다.
Main 내부의 `sdk/DobotDllType.py`와 `sdk/DobotDll.dll`을 사용합니다.
`sdk/`에는 Qt 및 Visual C++ 의존 DLL도 포함되어 있어 `DobotControl_Test` 폴더 없이
Main을 사용할 수 있습니다. Python 의존 패키지와 장비 USB 드라이버는 PC별로 설치해야 합니다.
연결 진단과 포트 복구도 기본적으로 이 내부 SDK를 사용하며, `--sdk-dir`로 변경할 수 있습니다.

```powershell
python -m manual_tests.dobot_connection_test
```

특정 포트만 검사하려면 다음과 같이 실행합니다.

```powershell
python -m manual_tests.dobot_connection_test --ports COM3 COM4 COM5 COM6
```

포트 한 개가 응답하지 않을 때 기다릴 시간은 기본 8초입니다. 다음처럼 변경할 수
있습니다. 한 포트가 멈춰도 제한 시간이 지나면 실패로 기록하고 다음 포트를 계속
검사합니다.

```powershell
python -m manual_tests.dobot_connection_test --port-timeout 10
```

성공 조건은 기본적으로 4대 모두 연결되는 것입니다. 결과는 `logs` 폴더에 JSON으로
저장됩니다. 이 점검에서는 HOME, JOG, PTP, 그리퍼, 흡착컵 명령을 전송하지 않습니다.

## 로봇 등록

물리적인 로봇을 확인한 뒤 각 포트를 명시적으로 등록합니다. 포트를 임의로 지정하면
다른 로봇이 움직일 수 있으므로 실제 장비와 대조한 후 실행해야 합니다.

```powershell
python .\dobot_config.py --assign Dobot_1 COM14
python .\dobot_config.py --assign Dobot_2 COM11
python .\dobot_config.py --assign Dobot_3 COM13
python .\dobot_config.py --assign Dobot_4 COM15
```

등록 상태만 확인하려면 인자 없이 실행합니다.

```powershell
python .\dobot_config.py
```

## 4대 동시 상태 확인

네 포트를 모두 등록한 후 실행합니다.

```powershell
python -m manual_tests.multi_dobot_status_test
```

각 Dobot은 별도 프로세스에서 SDK를 로드하므로 SDK의 전역 장치 ID가 서로 충돌하지
않습니다. Worker는 연결·상태 읽기뿐 아니라 검증된 JOG, PTP, HOME, 큐 및 공구 명령도
처리합니다. 모든 명령은 시간 제한과 입력 검사를 거치며 무응답 Worker는 종료됩니다.

한 Worker 장애가 나머지 Dobot에 전파되지 않는지 시험하려면 다음을 실행합니다.
로봇 이동 없이 Dobot_1의 Python Worker만 종료한 후 자동 복구를 확인합니다.

```powershell
python -m manual_tests.worker_isolation_test
```

## 설정 자동 테스트

포트와 엔드이펙터 매핑이 정확히 저장되는지 실제 로봇을 움직이지 않고 검사합니다.

```powershell
python -m unittest tests.test_dobot_config -v
```

## 장치 고유 식별정보 확인

COM 포트가 바뀌었을 때 자동으로 같은 Dobot을 찾을 수 있는지 확인합니다. 로봇은
움직이지 않으며 SDK 시리얼번호와 장치명만 읽습니다.

```powershell
python -m manual_tests.dobot_identity_test
```

## 변경된 COM 포트 자동 복구

최초 등록된 네 Dobot의 고유 시리얼번호를 기준으로 현재 COM 포트를 찾아 설정을
자동 갱신합니다. 네 대를 모두 찾지 못하면 기존 설정을 변경하지 않습니다.

```powershell
python .\dobot_port_recovery.py
```

## Dobot_1 저속 조그 방향 시험

먼저 `--execute` 없이 입력값과 등록 포트만 확인합니다. 이때 로봇은 움직이지 않습니다.

```powershell
python -m manual_tests.single_robot_jog_test --axis z --direction +
python -m manual_tests.single_robot_jog_test --robot Dobot_2 --axis r --direction +
```

컨베이어 등 주변 간섭이 없고 비상정지를 사용할 준비가 된 경우에만 실제 시험을
실행합니다. 한 번의 명령은 최대 0.25초, 속도는 최대 10%로 제한되며 현재 알람이
있으면 자동으로 차단됩니다.

```powershell
python -m manual_tests.single_robot_jog_test --axis z --direction + --duration 0.10 --speed 5 --execute
```

이동 명령은 통신 오류가 발생해도 자동 재시도하지 않습니다. 시험할 때는 항상 로봇
옆에서 비상정지를 준비하고, 먼저 안전한 Z 방향부터 아주 짧게 확인하세요.

## JOG 즉시 정지 시험

조그 시작과 정지를 별도 명령으로 보내 즉시 정지 기능을 확인합니다. 정지 명령이
누락되어도 Worker의 감시 시간이 끝나면 최대 0.25초 안에 자동 정지합니다.

```powershell
python -m manual_tests.jog_immediate_stop_test
python -m manual_tests.jog_immediate_stop_test --axis r --direction + --execute
python -m manual_tests.jog_immediate_stop_test --robot Dobot_2 --axis r --direction + --execute
```

## PTP 이동과 도착 확인 시험

현재 좌표를 기준으로 한 축만 조금 이동하며, 목표 오차 범위에 3회 연속 들어온 경우에만
도착으로 판정합니다. 시험 이동량은 축별 최대 5mm 또는 R축 5도로 제한됩니다.

```powershell
python -m manual_tests.ptp_arrival_test --axis r --delta 0.5
python -m manual_tests.ptp_arrival_test --axis r --delta 0.5 --execute
python -m manual_tests.ptp_arrival_test --robot Dobot_2 --axis r --delta 0.5 --execute
```

## 큐 시작·정지·초기화 시험

이동 명령을 넣지 않은 빈 큐로 컨트롤러의 초기화, 시작, 인덱스 읽기, 정지 기능을
검사합니다. 따라서 실기 시험에서도 로봇 좌표는 변하지 않아야 합니다.

```powershell
python -m manual_tests.queue_control_test
python -m manual_tests.queue_control_test --execute
```

## 강제 즉시 정지 경로

`RobotMotionController.emergency_stop()`은 일반 명령 Queue와 분리된 Event를 통해
`DobotWorker.force_stop()`을 호출합니다. 따라서 Worker가 PTP 또는 기계적 HOME의
완료를 기다리는 중에도 최대 약 20ms 간격으로 정지 요청을 확인하여 JOG 정지,
큐 강제 정지, 남은 큐 삭제를 차례로 시도합니다. 강제 정지 경로가 실패하면 기존
JOG 정지와 일반 큐 정지도 대체 경로로 시도합니다.

이 기능은 PC 프로그램 수준의 보조 안전 기능입니다. USB 통신, Windows 또는
Dobot 컨트롤러 자체가 멈춘 상황까지 보장하지 않으므로 실제 설비의 하드웨어
비상정지 회로를 대신할 수 없습니다.

## 그리퍼·흡착컵 공통 인터페이스 시험

`robots.json`의 엔드이펙터 설정에 따라 그리퍼는 열기·닫기, 흡착컵은 ON·OFF를
시험한 뒤 제어 출력을 비활성화합니다.

```powershell
python -m manual_tests.end_effector_test --robot Dobot_1
python -m manual_tests.end_effector_test --robot Dobot_1 --execute
```

## 알람 조회·초기화

조회만 할 때는 로봇 상태를 변경하지 않습니다. `--clear`를 추가하면 조그와 큐를
먼저 정지한 뒤 알람 표시를 초기화하고, 남은 원인으로 알람이 재발하는지도 확인합니다.

```powershell
python -m manual_tests.alarm_control_test
python -m manual_tests.alarm_control_test --clear
```

## 명령 시간 제한과 오류 처리 시험

실제 이동 범위를 벗어난 PTP 목표를 보내 Worker의 안전 검사에서 거부되는지 확인한
뒤, 같은 Worker로 상태를 다시 읽어 오류가 다음 명령에 전파되지 않는지 검사합니다.

```powershell
python -m manual_tests.command_error_handling_test
```

## 티칭 포인트 관리

먼저 모든 필수 포인트가 미등록된 JSON 파일을 생성합니다. 초기화만으로 현재 로봇
좌표가 저장되지는 않습니다.

```powershell
python .\teaching_point_manager.py --init
python .\teaching_point_manager.py --list
```

작업자가 로봇을 원하는 위치로 조그한 뒤 현재 좌표를 저장할 때만 `--save`를
사용합니다. 같은 포인트를 다시 저장하면 이전 좌표가 새 좌표로 교체됩니다.

```powershell
python .\teaching_point_manager.py --capture Dobot_1 ready
python .\teaching_point_manager.py --capture Dobot_1 ready --speed 5 --save
```

GUI에서 로봇을 선택하고 JOG로 위치를 조정한 뒤 현재 좌표를 저장할 수 있습니다.
JOG 속도는 1~100%이며 버튼을 누르는 동안 연속 이동하고 놓으면 즉시 정지합니다.
정지 신호가 누락돼도 0.25초 감시 시간이 끝나면 자동 정지합니다. HOME 버튼은
두 종류입니다. `현재 위치를 사용자 READY로 저장`은 티칭 좌표를 저장하고,
`기계적 HOME 이동`은 확인창 이후 컨트롤러의 원점 복귀 절차를 실행합니다.

```powershell
python .\teaching_gui.py
```

## HOME 및 안전 이동 순서 검증

기본 HOME 경로는 `기계적 HOME → 사용자 ready` 순서입니다. 작업자가 위치를 직접
판단할 때는 HOME 없이 READY 버튼으로 공정 대기를 승인할 수 있습니다. 공정 중에는
픽업 상승 위치와 조립·반환 접근 위치를 경유하고, 끝나면 사용자 `ready`로 돌아옵니다.
Dobot_3은 `_1 → pickup`으로 진입하고 `_1 → _2` 순서로 빠져나옵니다.
그리퍼 로봇은 닫기 후 2초 동안 파지가 안정되기를 기다린 뒤 상승합니다. 배치할
때도 그리퍼를 연 뒤 2초 기다리고 엔드이펙터 제어를 해제합니다. 이 대기 중에도
프로그램 비상정지 상태를 약 50ms 간격으로 확인합니다.
Dobot_3은 흡착컵을 켠 뒤 1초 동안 흡착을 유지한 다음 픽업 위치에서 상승합니다.

기본 명령은 계획만 출력하는 DRY-RUN입니다. 실제 검증은 반드시 한 대씩 실행하며,
`--execute` 후 화면에 선택한 로봇 이름까지 다시 입력해야 움직입니다.

```powershell
python -m manual_tests.home_safety_motion_test --robot Dobot_1 --mode home
python -m manual_tests.home_safety_motion_test --robot Dobot_1 --mode home --execute
python -m manual_tests.home_safety_motion_test --robot Dobot_1 --mode cycle --destination assembly_1 --execute
```

## 모바일 티칭 웹 화면

같은 Wi-Fi/LAN의 휴대폰에서 4대 상태 확인, 단일 제어권, 직교·관절 JOG,
JOG 즉시정지, 티칭 포인트 목록 확인과 현재 실제 좌표 저장·수정을 제공합니다.
현재 단계에는 포인트 PTP 이동과 HOME 기능을 넣지 않았습니다. PC 통합 GUI·티칭
GUI와 동시에 같은 COM 포트를 사용할 수 없으므로 다른 Dobot 프로그램을 먼저
종료한 뒤 모바일 서버만 실행합니다.

```powershell
cd ./Dobot
python mobile_teaching_server.py --lan
```

PowerShell에 표시되는 `http://PC주소:8765`를 휴대폰 브라우저에 입력한다. Windows
방화벽 안내가 표시되면 공용 네트워크가 아닌 현재 사용하는 개인 네트워크만 허용한다.
휴대폰에서 로봇을 선택하고 `제어권 획득`을 누른 뒤 JOG 또는 티칭 저장을 사용합니다.
제어권은 한 화면만 가질 수 있고 휴대폰 통신이 끊기면 약 15초 뒤 자동 만료됩니다.
JOG는 버튼 생존 신호가 약 0.25초 동안 오지 않으면 Worker가 자동 정지합니다.
현재 좌표 저장은 선택 포인트, 속도 1~100%, 확인 대화상자를 거쳐 수행하며 알람이
있는 로봇은 저장을 차단합니다.

## 공정 상태 머신

`robot_control/process_state_machine.py`는 PLC 기동 신호를 로봇별 공정 실행 조건으로
변환합니다. 실제 운전에서는 KEPServerEX OPC-UA 입력을 `ProcessSignals`로 변환하고,
서버가 없는 경로 검사에서는 GUI 모의 입력을 같은 형식으로 전달합니다.

- Dobot_1: 공급 완료와 비전 OK가 모두 있어야 단일 조립
- Dobot_2: 1회이면 `assembly_1`, 2회이면 첫 작업 후 두 번째 공급을 기다려 `assembly_2`
- Dobot_3: 비전 OK와 AGV 공급 완료가 모두 있어야 조립
- Dobot_4: 제품 도착 후 Main, Lamp_a, Lamp_b, Seat_a, Seat_b 중 반환 위치 선택
- 공통 상태: 연결 해제, HOME, 대기, 두 번째 공급 대기, 운전, 일시정지, 오류

HOME 초기화가 끝나지 않은 로봇의 공정 시작은 차단하며, 실행 오류나 비상정지가
발생하면 오류 상태로 전환합니다.

## PC 모의 신호

`mock_signal_source.py`는 서버 없이 경로와 상태 전이를 검사하기 위한 입력을 메모리에서
생성합니다. 실제 설비에서는 PLC가 비전·AGV 조건을 먼저 처리하고 최종 START를 주며,
OPC-UA 입력도 같은 `ProcessSignals`를 사용하므로 공정 상태 머신을 공유합니다.

공급 완료·AGV 완료·제품 도착 신호가 계속 ON인 경우에는 공정을 한 번만 실행하며,
신호가 OFF로 복귀한 뒤 다시 ON돼야 다음 공정을 허용합니다. 비전 신호가 먼저
들어오거나 나중에 들어오는 경우처럼 입력 순서가 달라도 전체 조건이 만족되면 한 번
실행합니다. 다음 명령은 실제 로봇에 연결하지 않는 모의 시험입니다.

```powershell
python -m manual_tests.mock_signal_test
```

## 통합 운전 GUI

`integrated_gui.py`는 4대 로봇의 공정 상태, PC 모의 입력, 개별·전체 HOME,
공정 시작, 작업 초기화, GUI 비상정지와 작업 로그를 한 화면에 표시합니다.
HOME·공정 경로 검사 버튼은 실제 `RobotMotionController`와 현재 티칭 좌표를
사용하여 이동 순서와 필수 포인트를 검사하지만 Worker의 이동 함수를 호출하지 않는
DRY-RUN입니다. 따라서 실제 Dobot을 움직이지 않습니다. 화면 위쪽의
`실제 4대 연결 및 상태 읽기`는 등록된 네 COM 포트에 연결하여 현재 X/Y/Z/R 좌표와
알람만 읽으며 HOME, JOG, PTP 이동 명령은 보내지 않습니다.

```powershell
python .\integrated_gui.py
```

사용 순서는 `선택/전체 HOME 경로 검사 → 로봇별 신호 입력 → 공정 경로 검사`
입니다. 실제 장비 상태 확인은 `실제 4대 연결 및 상태 읽기 → 실제 좌표 새로고침 →
실제 4대 연결 해제` 순서로 사용할 수 있습니다. `티칭 GUI 열기`를 누르면 기존의
연속 JOG, 엔드이펙터 수동 동작, HOME, 포인트 저장·수정·이동 기능을 보조 창으로
사용할 수 있습니다. 같은 COM 포트의 중복 사용을 막기 위해 통합 상태 연결을 먼저
해제해야 티칭 창이 열립니다.

네 로봇의 실제 연결이 모두 성공하면 선택 로봇 한 대의 `실제 HOME 실행`,
`PLC READY 승인`, `실제 1사이클 실행` 버튼이 준비됩니다. 실제 이동 직전에는 간섭 확인 질문에
동의한 뒤 선택한 `Dobot_1` 같은 로봇 이름을 정확히 다시 입력해야 합니다. 실제
수동 `실제 1사이클 실행`은 최대 10%이고, PLC START 자동운전은 저장된 포인트
속도를 따르되 최대 30%입니다. 예를 들어 저장 속도가 20%면 자동운전은 20%,
40%면 자동운전은 30%로 이동합니다.
`4대 전체 동시 원점복귀`는 네 Worker에서 병렬 실행됩니다. 자동 모드에서는 네 로봇이
서로 독립적으로 각자의 PLC START를 처리하므로 동시에 공정이 시작될 수 있습니다.
로봇 간 작업영역이 겹치는 설비에서는 PLC 인터록과 실제 간섭 검증이 반드시 필요합니다.
GUI의 비상정지는 연결된 실제 Worker에도 강제 정지를 요청하지만 안전등급
하드웨어 비상정지 회로를 대체하지 않습니다.

Dobot_2의 두 번째 공급 대기와 `assembly_2`까지 통합 계층으로 검증할 때는 아래
명령을 사용합니다. 실제로 `assembly_1`과 `assembly_2` 두 사이클이 실행되므로
주변 간섭과 재료 준비를 모두 확인해야 합니다.

```powershell
python -m manual_tests.integrated_live_cycle_test --robot Dobot_2 --work-count 2 --execute
```

Dobot_4는 판별된 제품 종류에 따라 다섯 반환 위치 중 하나를 선택합니다.

```powershell
python -m manual_tests.integrated_live_cycle_test --robot Dobot_4 --product-type Main --execute
python -m manual_tests.integrated_live_cycle_test --robot Dobot_4 --product-type Lamp_a --execute
python -m manual_tests.integrated_live_cycle_test --robot Dobot_4 --product-type Lamp_b --execute
python -m manual_tests.integrated_live_cycle_test --robot Dobot_4 --product-type Seat_a --execute
python -m manual_tests.integrated_live_cycle_test --robot Dobot_4 --product-type Seat_b --execute
```

## 운전 전 최종 체크리스트

1. 네 로봇 전원, USB 허브 전원, 케이블 고정을 확인합니다.
2. `dobot_connection_test`에서 정확히 4대가 성공했는지 확인합니다.
3. COM이 달라졌으면 `dobot_port_recovery.py`를 실행합니다.
4. `dobot_identity_test`로 로봇 이름과 시리얼번호가 일치하는지 확인합니다.
5. `multi_dobot_status_test`로 좌표·관절·알람과 Worker 종료를 확인합니다.
6. 티칭 포인트가 임시값인지 최종값인지 작업자가 확인합니다.
7. 통합 GUI에서 실제 4대 연결 후 4대 동시 원점복귀를 수행합니다.
8. 각 카드가 `READY ON`, KEPServerEX 태그도 `True`인지 확인합니다.
9. 수동 모드에서 PLC START는 표시만 되고 로봇이 기동하지 않는지 확인합니다.
10. 자동 모드는 PLC·컨베이어·AGV·비전 인터록과 비상정지 준비 후 사용합니다.

자동 테스트 통과는 코드 로직의 회귀 오류가 없다는 뜻이며, 실제 좌표와 설비 간섭이
안전하다는 뜻은 아닙니다. 티칭 좌표 변경 후에는 반드시 저속으로 한 대씩 경로를 다시
검증해야 합니다. Python 비상정지는 하드웨어 안전회로를 대체하지 않습니다.

## 현재 남아 있는 운영 확인 사항

- Dobot_4 제품 종류 태그는
  `ns=2;s=M.PLC.VISION_I/O.VISION4_CLASS_RESULT`로 확정되었습니다.
- PLC ERROR 상세 코드용 데이터 레지스터는 아직 정하지 않았습니다.
- 모든 티칭 좌표는 설비 최종 배치에 따라 GUI에서 다시 조정될 수 있습니다.
- KEPServerEX가 일시적으로 끊기면 현재 신호 교환 스레드는 안전하게 종료됩니다.
  재접속 자동화는 후속 작업이며, 현재는 실제 4대 연결을 해제한 뒤 다시 연결합니다.
- 모바일 티칭은 상태·JOG·현재 좌표 저장 단계까지 구현됐습니다. 포인트 이동,
  HOME 및 공구 동작은 실제 안전 검증 후 추가할 예정이며 최종 자동운전은 PC 통합
  GUI에서 수행합니다.
