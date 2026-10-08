# PLC 래더 및 공정 연동 자료

PBV 조립 공정 PLC 프로그램의 공개용 자료입니다. PLC 개발은 팀 산출물로 표시하며 포트폴리오 소유자의 Dobot 통합 제어와 연결되는 신호를 설명합니다.

## 읽는 순서
1. [2026-10-02 래더 PDF - 69쪽](docs/PLC_래더_2026-10-02.pdf)
2. [2026-09-25 GX Works2 CSV 내보내기 - 9개](ladder-export/2026-09-25/)
3. [Dobot OPC UA 태그](../Dobot/docs/ROBOT_OPCUA_TAGS.md)
4. [SCADA 구축·운영 자료](../SCADA/README.md)

## 버전 구분
| 자료 | 시점 | 용도 |
|---|---|---|
| 래더 PDF | 원본 출력일 2026-10-02 | 프로그램 설정 및 래더 확인 |
| CSV 9개 | 원본 폴더 2026.09.25, 일부 파일 저장일 09-26 | 당시 1_Main~9_servo의 명령·디바이스·주석 확인 |
| 비공개 incheonProject_Final.gxw | 로컬 수정일 2026-10-06 | 최종 이름의 원본. 이번 공개본에 미포함 |

PDF와 CSV는 서로 다른 시점입니다. 둘을 10월 6일 GXW 최종본의 동일한 내보내기로 간주하지 않습니다. CSV는 원본의 탭 구분 형식을 유지하고 UTF-16에서 UTF-8로 변환했습니다. 확장자는 원본 .csv를 유지합니다.

## 공정과 Dobot 연동
프로젝트 문서의 PLC 장비는 Q03UDECPU, QX41, QY81P, QD75D1N, QJ71E71-100입니다.
CSV는 Main·Body·Lamp·Seat·Robot·Vision·Return·Vision4/회수·Servo 프로그램으로 구분됩니다.
SCADA 최종 재구축 문서 기준 프로그램 선택은 D0, 생산 START는 M90의 500ms 펄스입니다. Dobot는 OPC UA START/READY/RUNNING/FINISH/ERROR 태그로 공정을 연계합니다. 실제 태그 주소는 각 시점의 PLC 프로젝트와 대조해야 합니다.

## 공개 범위와 검증
69쪽 래더 PDF의 전체 텍스트와 CSV 9개에서 내부 IP, 암호 관련 표현, 개인 사용자 경로를 검사했습니다.
원본 232쪽 전체 PDF에는 내부 주소 및 암호 관련 설정 페이지가 있어 제외했습니다. GXW·HMI·서보 설정·과거 시험 프로젝트는 이번 공개 범위에 포함하지 않았습니다.
원본은 수정하지 않았습니다. 실제 GX Works2 열기·컴파일·PLC 다운로드·장비 시운전은 수행하지 않았습니다.
