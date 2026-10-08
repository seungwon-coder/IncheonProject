"""Dobot 4대의 통합 상태 표시와 수동·자동 운전 GUI 패키지."""

# 기존 ``from integrated_gui import IntegratedDobotGui`` 코드도 계속 사용할 수
# 있도록 메인 창 클래스를 패키지 바깥으로 공개한다.
from integrated_gui.integrated_gui import IntegratedDobotGui

__all__ = ["IntegratedDobotGui"]

