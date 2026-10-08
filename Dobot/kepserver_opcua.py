"""기존 실행 명령을 유지하기 위한 KEPServerEX OPC-UA 도구 진입 파일."""

from communication.kepserver_opcua import main


if __name__ == "__main__":
    raise SystemExit(main())

