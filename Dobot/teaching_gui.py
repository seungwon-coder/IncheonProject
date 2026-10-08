"""기존 실행 명령을 유지하기 위한 PC 티칭 GUI 진입 파일."""

import multiprocessing as mp

from teaching.teaching_gui import main


if __name__ == "__main__":
    mp.freeze_support()
    main()

