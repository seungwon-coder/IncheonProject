# SCADA 공개용 선별 자료

2026-10-08 SCADA/최종 자료 기준. CIMON SCADA 프로젝트는 팀 산출물입니다.

## 포함
- helpers/VISION_MJPEG_RELAY_4CH.py: Vision 4채널 MJPEG 화면 중계 보조 코드. 기본 주소는 localhost 예시.
- helpers/ROBOT_TCP_WATCH.py: SCADA PC의 TCP Established 연결 여부를 상태 파일로 기록하는 보조 코드. 로봇 동작 성공·안전 상태를 판정하는 코드가 아닙니다.
- database/smart_factory_tables.sql: 원본 백업에서 추출한 7개 테이블 정의. 운영 주문, 사용자 계정, 생산 이력, 실제 데이터는 없습니다. 원본의 7개 뷰와 CIMON 생산 제어 스크립트는 포함하지 않습니다.

## 생산 제어 구조
CIMON 생산 화면/스크립트와 KEPServer 태그가 실제 구성의 중심입니다. ORDER.PLC_PROGRAM_NO는 D0, ORDER.PLC_START는 M90에 연결되며 START는 500ms 펄스입니다.
최종 정상 완료는 ROBOT_FINISH_3(M1023), NG는 VISION3_NG(M1124) 기준입니다. 기존 D200 설명 및 Python PLC_FINISH/Quick Client 시험 코드를 최종 생산 구현으로 해석하지 않습니다.

## 범위
이 ZIP은 SCADA 전체 복원본이나 CIMON 설치 파일이 아닙니다. PGX/SCX/PRJ/OPF 등 원본 바이너리 프로젝트와 실제 DB 백업은 계정·내부 설정이 포함될 수 있어 비공개 원본으로 보관했습니다. 실제 CIMON 프로젝트의 화면·스크립트 복원에는 비공개 원본과 정식 실행 환경이 필요합니다.
로컬 원본은 수정하지 않았습니다. 보조 코드는 실행하면 네트워크 읽기와 상태 파일 기록이 시작되므로 시험 환경 설정 후 사용합니다. 본 공개 준비에서 실행하지 않았습니다.

## 담당 구분
SCADA 구축은 팀 산출물로 표기합니다. 포트폴리오 소유자의 Dobot 제어 코드와 연결되는 태그/상태를 설명할 수 있으나 SCADA 전체 개발을 단독 기여로 주장하지 않습니다.

## 포트폴리오 자료
- [SCADA·MES 프로젝트 소개](docs/SCADA_MES_프로젝트_소개.pdf)
- [SCADA 구축·운영 안내](docs/SCADA_구축_운영_안내_공개용.pdf)
- [주요 화면](screenshots/)
- [MES 및 106개 태그 수집기](../MES/README.md)
- [PLC 래더 자료](../PLC/README.md)
