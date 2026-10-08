"""기존 실행 명령을 유지하기 위한 Dobot 통합 GUI 진입 파일."""

import multiprocessing as mp

from integrated_gui.integrated_gui import IntegratedDobotGui


if __name__ == "__main__":
    mp.freeze_support()
    IntegratedDobotGui().mainloop()

