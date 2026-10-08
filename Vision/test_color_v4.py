"""V4 클래스 의미가 결과코드 1~6으로 변환되는지 확인합니다."""
from vision_core_server import Detection, decide

s={"id":4,"inspection":"sort","classes":{}}
def d(value):return Detection(name=value,value=value,score=.99,box=[0,0,1,1],polygon=[])
for value,code in (("round",2),("edge",3),("cocoa",4),("dark",5),("body",6)):
    assert decide(s,[d(value)],0,.8)==("OK",code)
assert decide(s,[],0,.8)==("OK",1)
print("PASS: V4 codes 1~6")
