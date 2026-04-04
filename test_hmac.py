import hmac
import hashlib

canonical = "heartbeat|1775243440"
SECRET_KEY = "H4ckath0n_TrU5t_K3y_99!"
expected_sig = hmac.new(SECRET_KEY.encode('utf-8'), canonical.encode('utf-8'), hashlib.sha256).hexdigest()
print(f"Canonical: {canonical}")
print(f"Expected Sig: {expected_sig}")
print(f"ESP32 Sig   : e5926986efd4f7cbcb82fb2ffdb65c491eefe1415b778e1e2bd095289d0a6c8d")
print("Match?", expected_sig == "e5926986efd4f7cbcb82fb2ffdb65c491eefe1415b778e1e2bd095289d0a6c8d")
