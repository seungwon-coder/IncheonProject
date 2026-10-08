import json
from pathlib import Path

cfg=json.loads(Path(__file__).with_name('config.json').read_text())
stations={s['id']:s for s in cfg['stations']}
expected={
 1:{'product':'VISION1_PRODUCT','start':'VISION1_START','running':'VISION1_RUNNING','finish':'VISION1_FINISH','ng':'VISION1_NG','error':'VISION1_ERROR'},
 2:{'product':'VISION2_PRODUCT','start':'VISION2_START','running':'VISION2_RUNNING','finish':'VISION2_FINISH','ng':'VISION2_NG','error':'VISION2_ERROR'},
 3:{'product':'VISION3_PRODUCT','start':'VISION3_START','running':'VISION3_RUNNING','finish':'VISION3_FINISH','ng':'VISION3_NG','error':'VISION3_ERROR'},
 4:{'result':'VISION4_CLASS_RESULT','start':'VISION4_START','running':'VISION4_RUNNING','finish':'VISION4_FINISH','ng':'VISION4_NG','error':'VISION4_ERROR'},
}
for vision,tags in expected.items():
    assert set(stations[vision]['tags'])==set(tags)
    for key,name in tags.items():assert stations[vision]['tags'][key].endswith(name)
for vision in (1,2,3):assert stations[vision]['tags']['product']!=stations[vision]['tags']['start']
assert stations[4]['tags']['result']!=stations[4]['tags']['start']
assert stations[1]['inspection']=='lamp' and stations[2]['inspection']=='seat'
assert 'result' not in stations[1]['tags'] and 'result' not in stations[2]['tags']
print('PASS: actual V1-V4 tags, V1 lamp/V2 seat roles, V1/V2 binary NG without extra CLASS_RESULT')
