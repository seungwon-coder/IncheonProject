"""실제 영상을 열지 않고 config.json의 네 카메라 경로 매핑만 검사합니다."""
import json
from pathlib import Path

from camera_identity import resolve_all

root = Path(__file__).resolve().parent
config = json.loads((root / "config.json").read_text(encoding="utf-8"))
stations = [s for s in config["stations"] if s.get("enabled", True)]

print("저장된 인스턴스/장치 경로를 현재 OpenCV 번호로 변환합니다.\n")
for station_id, (index, backend, device) in sorted(resolve_all(stations).items()):
    print(f"비전{station_id}: 번호={index}, 백엔드={backend}, 이름={device.name}")
    print(f"  경로={device.path}")
print("\n중복 없이 모든 카메라 매핑 완료")
