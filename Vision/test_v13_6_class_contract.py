from vision_core_server import required_model_values

assert required_model_values("lamp") == {"round", "edge"}
assert required_model_values("seat") == {"seat_unknown"}
assert required_model_values("assembly") == {"seat_unknown"}
assert required_model_values("sort") == {"round", "edge", "seat_unknown", "body"}
print("PASS: V13.7 attached-model class contract")
