import json
from pathlib import Path

config=json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8-sig"))
assert config["timeout_seconds"]==3
assert {station["id"] for station in config["stations"] if station["enabled"]}=={1,2,3,4}
print("PASS: V1-V4 share the three-second inspection timeout")
