"""Display-only checks; no hardware or camera settings changed."""
from display_layout import fitted_size
assert fitted_size((640,480),(1000,750))==(1000,750)
assert fitted_size((640,480),(1000,400))==(533,400)
assert fitted_size((640,546),(800,400))==(468,400)
assert fitted_size((640,480),(320,240))==(320,240)
assert fitted_size((640,480),(1,1))==(1,1)
print('PASS: expands beyond old 520x360 cap, preserves aspect ratio, fits compact strip and small windows')
