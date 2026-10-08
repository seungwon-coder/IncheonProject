from engine import decide, validate_model

def station(n):return {'id':n,'classes':{}}
def ds(*names):return [{'name':n,'score':.99} for n in names]
validate_model(station(1),'detect',['car lamp_A','car lamp_R'])
assert decide(station(1),ds('car body','car lamp_R','Gloss/Standard'),1,.8)==('OK',0)
assert decide(station(1),ds('car lamp_A'),1,.8)==('NG',0)
assert decide(station(2),ds('car body','Gloss/Standard','car lamp_R'),1,.8)==('OK',0)
assert decide(station(2),ds('Matte Brown'),1,.8)==('NG',0)
assert decide(station(2),ds('car seat'),1,.8) is None
assert decide(station(3),ds('car body','lamp_on','Gloss/Standard','Gloss/Standard'),1,.8)==('OK',0)
assert decide(station(3),ds('car body','Gloss/Standard','Gloss/Standard'),1,.8)==('NG',0)
assert decide(station(2),ds('car body'),1,.8) is None
assert decide(station(2),ds('car lamp_R','car lamp_A'),1,.8) is None
assert decide(station(4),ds('car body'),1,.8)==('OK',6)
try:validate_model(station(1),'detect',['unconfirmed'])
except ValueError:pass
else:raise AssertionError('unknown allowed')
print('PASS: V1 lamp/V2 seat aliases, V3 body-seat-lampON, V4 mapping')
