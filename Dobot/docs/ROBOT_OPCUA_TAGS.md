# Dobot OPC UA 신호 목록

서버: `opc.tcp://127.0.0.1:49320`

그룹: `ns=2;s=M.PLC.ROBOT_I/O`

운전 신호는 `communication/robot_opcua_tags.py`, 좌표 태그는
`communication/robot_position_opc.py`에서 관리합니다.
아래 `(n)`은 `(1)`~`(4)`이며 각각 Dobot_1~Dobot_4에 대응합니다.

Dobot_4 차량 하부 출고 전용 태그:

- `ns=2;s=M.PLC.ROBOT_I/O.ROBOT_START(B)_(4)`: PLC → Python 베이스 공급 시작
- `ns=2;s=M.PLC.ROBOT_I/O.ROBOT_FINISH(B)_(4)`: Python → PLC 베이스 공급 완료 펄스

| 태그 이름 | 자료형 | 전달 방향 | 의미 |
|---|---|---|---|
| ROBOT_START_(n) | Boolean | PLC → Python | 해당 Dobot 기동 명령 |
| ROBOT_ERROR_(n) | Boolean | Python → PLC/SCADA | 해당 Dobot 오류 |
| ROBOT_FINISH_(n) | Boolean | Python → PLC/SCADA | 공정 완료 후 대기 위치 도착 |
| ROBOT_READY_(n) | Boolean | Python → PLC/SCADA | 대기 위치에서 다음 공정 준비 완료 |
| ROBOT_RUNNING_(n) | Boolean | Python → PLC/SCADA | 해당 Dobot 공정 실행 중 |
| ROBOT_Program_NO_(2) | UInt16 | PLC → Python | Dobot_2의 1회/2회 공정 선택 |
| ROBOT2_TARGET_COUNT | UInt16 | PLC → Python | Dobot_2 목표 공정 횟수 |
| ROBOT2_FINISH_COUNT | UInt16 | PLC → Python | PLC가 증가 및 초기화하는 Dobot_2 완료 횟수 |

## SCADA 좌표 태그

통합 GUI에서 실제 로봇 4대 연결에 성공하면 Python이 아래 Float 좌표를 약 1초마다
KEPServerEX에 기록하고 SCADA는 같은 태그를 읽습니다.

| 범위 | 자료형 | 전달 방향 | 의미 |
|---|---|---|---|
| `Coord.ROBOT1_X` ~ `Coord.ROBOT4_J4` | Float | Python → KEPServerEX → SCADA | 4대의 X/Y/Z/R/J1/J2/J3/J4 |

정확한 NodeId 형식은
`ns=2;s=M.PLC.ROBOT_I/O.Coord.ROBOT{번호}_{축}`입니다. 총 32개이며, 좌표는
새 COM 연결이 아니라 기존 Dobot Worker가 읽은 실제 `GetPose` 값을 사용합니다.

NodeId는 그룹 뒤에 `.`과 태그 이름을 붙인 값입니다. 대소문자를 그대로 유지합니다.

## 기존 코드 연결

`node_map_for_robot("Dobot_1")`은 기존 논리 신호인 `command.start`,
`status.busy`, `status.done`, `status.error`, `status.ready`를 실제 NodeId에 연결합니다.
이를 `OpcUaBackend(node_map=...)`에 전달할 수 있습니다.

Dobot_2의 프로그램 번호, 목표 횟수, 완료 횟수는 별도 신호로 등록했습니다.
자동 기동은 프로그램 번호를 사용하지 않고 목표 횟수와 완료 횟수만 검사합니다.
자동 모드는 목표 횟수를 `work_count`, 완료 횟수를 `completed_count`로 전달합니다.
기존 `communication/plc_io_map.py`의 M/D 주소와 실제 태그 간 대응도 이번 정보만으로 추정하지 않습니다.

## 확정된 Dobot_2 횟수 규칙

- PLC가 `ROBOT2_TARGET_COUNT`를 1 또는 2로 설정합니다.
- `ROBOT_FINISH_(2)=True`이고 `ROBOT2_TARGET_COUNT == ROBOT2_FINISH_COUNT`이면
  PLC가 `ROBOT2_FINISH_COUNT`와 `ROBOT2_TARGET_COUNT`를 모두 0으로 초기화합니다.
- 따라서 목표가 1이면 1회 완료 후, 목표가 2이면 2회 완료 후 위 조건으로 초기화합니다.
- PLC가 각 조립의 FINISH를 확인하고 완료 횟수를 1씩 증가시킵니다.
- Python은 완료 횟수와 목표 횟수를 읽기만 합니다.
- START 한 번에는 seat 하나만 조립합니다. 2회 프로그램도 두 번째 PLC START를 기다립니다.
- Program_NO 자체를 초기화하는 규칙은 지정되지 않았습니다.

### Program_NO=2일 때 순서

| 단계 | PLC 동작 | Dobot_2 동작 |
|---|---|---|
| 1 | TARGET_COUNT=2, 첫 seat 공급 후 첫 START | assembly_1 조립, 대기 위치 복귀 후 FINISH |
| 2 | FINISH_COUNT=1, 두 번째 seat 공급 후 다음 START | assembly_2 조립, 대기 위치 복귀 후 FINISH |
| 3 | FINISH_COUNT=2, 목표와 같으므로 FINISH_COUNT와 TARGET_COUNT를 0으로 초기화 | 다음 배치 기동 대기 |

실행 연동 시 START를 수락한 시점의 완료 횟수 0/1로 assembly_1/assembly_2를
선택하고, 실행 중 PLC의 카운터 갱신으로 조립 위치를 바꾸지 않아야 합니다.
자동 모드는 PLC 완료 횟수로 목적지를 선택하며 기존 수동 모드의 공정 순서는 유지합니다.

일반 FINISH와 Dobot_4 베이스 공급 FINISH는 각각 OPC-UA에 처음 ON을 기록한 뒤
약 1초 동안 유지합니다. ERROR는 작업자 초기화 후 READY를 다시 승인하면 해제됩니다.

통합 화면의 자동 모드를 선택하면 실제 PLC 응답 쓰기와 로봇 기동이 활성화됩니다.
개발 검증 과정에서는 실제 자동 기동이나 PLC 쓰기를 실행하지 않았습니다.
조회 시 Bad/None인 값을 정상 명령 또는 0으로 사용하면 안 됩니다.
