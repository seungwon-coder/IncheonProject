"""기존 실행 명령을 유지하기 위한 티칭 포인트 관리 진입 파일."""

import multiprocessing as mp

from teaching.teaching_point_manager import main


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())

