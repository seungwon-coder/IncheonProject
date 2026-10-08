# KEPServerEX OPC UA 통신

기본 접속 주소: `opc.tcp://127.0.0.1:49320`

## 실행 (Python 3.10 이상)

프로젝트 폴더에서 실행합니다.

```powershell
python -m pip install -r requirements-opcua.txt
python kepserver_opcua.py check
python kepserver_opcua.py browse --depth 3
```

ROBOT_I/O와 VISION_I/O 그룹의 태그 이름·자료형·현재 값·품질만 조회:

```powershell
python kepserver_opcua.py groups
```

특정 그룹만 조회하려면 NodeId를 지정합니다. 하위 폴더도 포함합니다.

```powershell
python kepserver_opcua.py groups "ns=2;s=M.PLC.ROBOT_I/O"
```

`None / Bad`는 유효한 현재 값을 받지 못했다는 뜻이며 0 또는 False가 아닙니다.
이 명령은 한 번 조회하고 종료합니다.

`browse` 결과에서 실제 NodeId를 복사하여 읽기/구독에 사용합니다.
아래 NodeId는 예시이며 실제 채널·디바이스·태그명과 namespace는 서버에서 확인해야 합니다.

```powershell
python kepserver_opcua.py read "ns=2;s=Channel1.Device1.Tag1"
python kepserver_opcua.py watch "ns=2;s=Channel1.Device1.Tag1"
```

`watch`는 250ms 간격을 요청하며 변경 값·품질·원본 시각을 출력합니다.
서버가 실제 간격을 조정할 수 있습니다. Ctrl+C로 종료합니다.
연결이 끊기면 오류와 함께 종료하며, 이 CLI는 자동 재연결하지 않습니다.

## 서버 설정

KEPServerEX의 OPC UA endpoint가 해당 IP와 포트에서 활성화되어 있어야 합니다.
서버 방화벽에서 클라이언트의 TCP 49320 접근이 허용되어야 합니다.
기본 실행은 익명/보안 None 연결이므로 서버가 이 구성을 허용해야 합니다.
운영 서버 보안 정책에 맞춰 다음 환경 변수/옵션을 설정합니다.

```powershell
$env:OPCUA_USERNAME = "사용자명"
$env:OPCUA_PASSWORD = "비밀번호"
$env:OPCUA_SECURITY = "Basic256Sha256,SignAndEncrypt,certs/client.der,certs/client-key.pem,certs/server.der"
python kepserver_opcua.py check
```

인증서는 별도 발급이 필요하며 KEPServerEX에서 클라이언트 인증서를 신뢰하도록
등록해야 합니다. `--application-uri`는 클라이언트 인증서의 Application URI와
일치시킵니다. 암호를 코드나 저장소에 기록하지 마세요.

## 기존 공정 코드와 연결

`OpcUaBackend`는 기존 `OpcConnectionManager` 인터페이스를 구현합니다.
실제 주소 매핑을 확정한 뒤 아래처럼 생성하여 사용합니다.

```python
from opcua_backend import OpcUaBackend
from opc_connection_manager import OpcConnectionManager

node_map = {"D1011": "ns=2;s=Channel1.Device1.WorkCount"}  # 실제 NodeId로 교체
connection = OpcConnectionManager(lambda: OpcUaBackend(node_map=node_map))
try:
    if connection.connect_if_due():
        print(connection.read(["D1011"]))
finally:
    connection.close()
```

공정 루프에서 `connect_if_due()`를 반복 호출하면 기존 관리자의 재접속 규칙을
사용할 수 있습니다. GUI 스레드가 아닌 작업 스레드에서 호출하세요.
GUI/실제 공정에 대한 자동 연결은 추가하지 않았습니다.

`backend.write({NodeId: value})`는 서버의 자료형으로 값을 기록합니다.
여러 태그 쓰기는 순차 처리이며 일부만 성공할 수 있습니다. 실패한 명령을
자동 재전송하지 않으며 실제 설비 쓰기는 이 점검 프로그램에서 실행하지 않습니다.

## 검증

```powershell
python -m unittest tests.test_opcua_backend tests.test_opc_connection_manager -v
```

로컬 시험 서버로 읽기·Int16 쓰기·구독·없는 노드 오류를 검사합니다.

참고: [asyncua 공식 문서](https://opcua-asyncio.readthedocs.io/en/latest/)
