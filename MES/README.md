# SCADA 연동 학습 MES - 공개용 소스

2026-10-08 로컬 project-mes 및 data_collect_all 기준. 팀 산출물이며 MES 출처는 김동진 프로젝트 산출물입니다. SCADA/MES 개발 전체를 포트폴리오 소유자의 단독 개발로 표시하지 않습니다.

## 구성과 현재 구현
- manufacturing/collector.py: 로봇 57, Vision 24, 주문 21, 라인 상태 4개, 총 106개 태그 수집 설계. 250ms 요청 변경값 구독, MySQL 최신값/이력, SQLite 임시 저장.
- manufacturing/mes: 주문·제품별 작업자 확인 기록, 재고, 검사 승인 및 PDF 보고서, NG 재생산 큐.
- scada_sync.py: smart_factory 주문·상품·생산 이력 읽기. MES DB에만 기록하며 SCADA 생산 명령은 쓰지 않습니다.
- SCADA 재생산 명령/수신 확인 및 ERP 전송은 WAITING_CONFIG 상태이며 미연결입니다.
- TEST/PRODUCTION은 데이터 분류입니다. PLC 운전 모드를 변경하지 않습니다.
- 제품별 공정 이력과 재고 일부는 작업자 입력입니다. 제품 추적 ID에 따른 전자동 연계는 구현되지 않았습니다.

## 실행 준비 (Windows)
1. Python, MySQL 및 requirements.txt 의존성을 준비합니다. 원본 환경의 정확한 Python 운용 버전은 이 패키지에서 재검증하지 않았습니다.
2. config.example.json을 config.json으로 복사하고 mysql_exe와 시험용 OPC UA endpoint를 설정합니다.
3. mysql-client.example.ini를 mysql-client.ini로 복사해 본인의 별도 테스트 DB 계정을 설정합니다.
4. MySQL에서 manufacturing/schema.sql, mes/schema.sql, mes/initialize-empty-inventory.sql 순서로 적용합니다. 초기 재고는 0이며 GUI에서 시험값을 입력합니다.
5. mes/scada-source.example.json을 scada-source.json으로 복사합니다. enabled=false가 기본입니다. SCADA 읽기 연동 사용 시에만 별도 읽기 계정과 scada-client.ini를 준비합니다.
6. manufacturing에서 `python collector.py`, 별도 터미널에서 `python mes/app.py` 실행 후 http://127.0.0.1:8765 접속합니다. 종료는 각 터미널에서 Ctrl+C.

공개용 사본은 웹 접속을 localhost로 제한했습니다. 화면의 작업자/관리자 선택은 로그인 인증이 아닙니다. 운영 네트워크 배포용 인증 서비스가 아닙니다.
실제 OPC UA 서버 태그, DB, MySQL 실행 파일이 없으면 설비 상태를 읽을 수 없습니다. 이 ZIP은 독립 실행형 설치 프로그램이 아닙니다.

## 공개용 변경
- 실제 계정/INI, 운영 DB, 로그, spool, 보고서, 수집 상태, 초기 주문 목록, 개인 PC 실행 파일을 제외.
- 내부 IP는 localhost 예시로 변경. 웹 LAN 바인딩 제거.
- 실제 운영 데이터 없이 테이블 정의만 제공. 초기 재고는 0.
- 기존 57개 태그 초기 버전과 구 github-upload 사본 대신 최신 106개 태그 및 MES 소스를 사용.
- 원본 수정 없음. 검증은 구문/구성/비공개 값 검사이며 실제 설비 운전 검증은 수행하지 않았습니다.

## 소스 출처와 담당 구분
원본: 프로젝트산출물_프로그래밍_김동진/MES.customDB/project-mes 및 data_collect_all.
프로젝트 인수인계에는 MES 팀원 저장소 inc6f4i/scada-mes, 참조 커밋 cbf1c4d가 기록되어 있습니다. 본 ZIP은 해당 원격 커밋과 동일함을 뜻하지 않습니다.
포트폴리오 소유자의 Dobot 제어 작업과 MES의 설비 상태 수집을 연결 지점으로 설명하며, SCADA/MES 직접 개발·시험 성과는 확인된 담당 범위와 증빙에 따라 별도로 표기해야 합니다.

## 포트폴리오 자료
- [DB 구조·연동 설명](docs/MES_DB_구조_및_연동설명.pdf)
- [주요 화면](screenshots/)
- [SCADA·MES 프로젝트 소개](../SCADA/docs/SCADA_MES_프로젝트_소개.pdf)
