from vision_core_server import Detection, vision3_detection_text

d1=Detection("car seat [seat1]","seat_unknown",.8,[0,0,10,10],None)
d2=Detection("car seat [seat2]","seat_unknown",.9,[10,0,20,10],None)
led=Detection("LED ROI","lamp_on",1,[0,0,5,5],None)
text=vision3_detection_text([d1,d2,led],.6)
assert text=="시트1 검출 / 시트2 검출 / LED ON"
assert "미분류" not in text
print("PASS: V3 explicit seat zones and LED result text")
