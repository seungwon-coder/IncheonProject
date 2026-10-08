from vision_core_server import Detection, decide


station={"id":3,"inspection":"assembly"}


def ds(values):
    return [Detection(value,value,.99,[0,0,10,10],None) for value in values]


# VAN: 시트 색상이 섞여도 시트 2개와 LED ON이면 OK
assert decide(station,ds(["body","cocoa","dark","lamp_on"]),1,.8)==("OK",0)
# TRUCK: 시트 색상과 무관하게 1개면 OK
assert decide(station,ds(["body","dark","lamp_on"]),5,.8)==("OK",0)
# 색상 미분류 시트도 시트 존재/수량으로 인정
assert decide(station,ds(["body","seat_unknown","lamp_on"]),5,.8)==("OK",0)
# 시트 누락, 수량 초과, LED OFF는 NG
assert decide(station,ds(["body","cocoa","lamp_on"]),1,.8)==("NG",0)
assert decide(station,ds(["body","cocoa","dark","cocoa","lamp_on"]),1,.8)==("NG",0)
assert decide(station,ds(["body","cocoa","dark","lamp_off"]),1,.8)==("NG",0)
assert decide(station,ds(["cocoa","dark","lamp_on"]),1,.8)==("OK",0)

print("PASS: V3 checks only ordered seat quantity and LED ON")
