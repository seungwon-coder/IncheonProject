"""기존 실행 명령을 유지하기 위한 COM 포트 복구 프로그램 진입 파일."""

import multiprocessing as mp

from robot_control.dobot_port_recovery import main


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())

