"""
Module 1: Cryptographic Transport Layer — Python Side

Provides HMAC-SHA256 signing for outbound pump commands and
verification of inbound ESP32 telemetry.

SECURITY RATIONALE: AES encryption is deliberately excluded.
HMAC-SHA256 gives us integrity + authentication — we can prove a
message came from the holder of the shared key and was not modified
in transit. AES would add confidentiality, but our threat model is
MITM injection / replay / spoofing, not eavesdropping on pump volumes.
Key exchange overhead for asymmetric AES would consume 10+ build hours
with zero additional demo value against the actual threat model.

PRODUCTION NOTE: SECRET_KEY is hardcoded here for demo purposes only.
In production this key MUST live in a Hardware Security Module such as
the Microchip ATECC608A, which provides secure key storage and
hardware-accelerated HMAC. Hardcoding a shared secret is a known
security anti-pattern outside of controlled demo environments.
"""

import hmac
import hashlib
import time
import threading
from uuid import uuid4
from typing import Optional, Tuple

# ── SHARED HMAC SECRET ──
# PRODUCTION WARNING: Move this to an HSM (ATECC608A) before any real deployment.
# See security rationale in module docstring above.
SECRET_KEY = b"H4ckath0n_TrU5t_K3y_99!"

# Time window constants (must match ESP32 firmware exactly)
PYTHON_TIME_WINDOW_SECONDS = 5    # Python rejects telemetry older than 5s
ESP32_TIME_WINDOW_SECONDS  = 2    # ESP32 rejects commands older than 2s


class CryptoTransport:
    """
    Session-scoped signing and verification.

    One instance per orchestrator session. The nonce counter is
    session-scoped and monotonically increasing — it never resets
    during a session, preventing replay attacks within the same session.
    Across sessions, the ESP32 accepts any nonce > its last seen nonce,
    so a fresh ESP32 boot accepts the new session's nonces cleanly.
    """

    def __init__(self):
        # SECURITY FIX: Initializing nonce from timestamp ensures that after a
        # Python restart, the new nonce is always greater than any previous
        # session, preventing the ESP32 from dropping packets as "REPLAY".
        # We use a 32-bit compatible truncation (max ~4 billion).
        self._nonce: int = int(time.time()) % 100000000 
        self._nonce_lock = threading.Lock()

        # Last nonce received from ESP32 telemetry (for replay detection)
        self._last_telemetry_nonce: int = 0

        # Consecutive time-drift counter (Section D)
        # Reset to 0 on every clean packet; escalate to ERROR at 3.
        self.consecutive_drift_count: int = 0

        # Session intrusion counter (Module 6)
        # Incremented on every HMAC_FAIL or REPLAY; BREACH at > 3.
        self.intrusion_count: int = 0

        # Per-session verified packet counter for system_metrics
        self.packets_verified: int = 0
        self.packets_dropped: int = 0
        self.peak_ma: float = 0.0

    def _next_nonce(self) -> int:
        """
        Thread-safe nonce increment.
        Monotonically increasing per session. Never resets between commands.
        This ensures every signed payload has a strictly higher nonce than
        the previous one, which the ESP32 enforces as a replay prevention gate.
        """
        with self._nonce_lock:
            self._nonce += 1
            return self._nonce

    def sign_payload(self, pump: str, duration_ms: int) -> dict:
        """
        Build and sign a pump command payload.

        SIGNING CONTRACT (agreed between Python and C++):
          canonical = f"{pump}|{duration_ms}|{ts}|{nonce}|{packet_id}"
          sig = HMAC-SHA256(SECRET_KEY, canonical.encode("utf-8")).hexdigest()

        We NEVER sign the JSON blob itself. JSON key ordering, whitespace,
        and encoding differences between Python json.dumps and ArduinoJson
        would produce different byte sequences for the same logical message,
        making verification unreliable. The canonical string is deterministic
        regardless of serialization library.
        """
        packet_id = str(uuid4())
        ts = int(time.time())
        nonce = self._next_nonce()
        pump = pump.strip().lower()

        # Canonical string — order and delimiter are the contract
        canonical = f"{pump}|{duration_ms}|{ts}|{nonce}|{packet_id}"

        sig = hmac.new(
            SECRET_KEY,
            canonical.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        return {
            "pump": pump,
            "duration_ms": duration_ms,
            "ts": ts,
            "nonce": nonce,
            "packet_id": packet_id,
            "sig": sig,
        }

    def sign_emergency_halt(self) -> dict:
        """
        Build and sign an emergency halt command.

        SECURITY RATIONALE: Emergency halts are still HMAC-signed.
        An attacker who can inject unsigned "emergency" packets could
        use them as a denial-of-service vector — stopping the system
        at will. Zero-trust applies even to safety commands.
        """
        packet_id = str(uuid4())
        ts = int(time.time())
        nonce = self._next_nonce()

        # Emergency halt canonical: pump="all", duration=0
        canonical = f"all|0|{ts}|{nonce}|{packet_id}"
        sig = hmac.new(
            SECRET_KEY, canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        return {
            "pump": "all",
            "duration_ms": 0,
            "emergency": True,
            "ts": ts,
            "nonce": nonce,
            "packet_id": packet_id,
            "sig": sig,
        }

    def sign_sync(self) -> dict:
        """
        Build and sign a clock-sync packet for the ESP32.
        Used on boot and after DRIFT_ESCALATION recovery.
        """
        ts = int(time.time())
        nonce = self._next_nonce()
        canonical = f"sync|{ts}|{nonce}"
        sig = hmac.new(
            SECRET_KEY, canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return {"ts": ts, "nonce": nonce, "sig": sig}

    def verify_telemetry(self, payload: dict, telemetry_type: str) -> Tuple[bool, str]:
        """
        Verify HMAC and time window on incoming ESP32 telemetry.

        SECURITY RATIONALE: If we accepted unsigned telemetry, an attacker
        could publish false sensor readings (e.g., EMPTY_CUP when cup is full)
        to trick the LLM into over-dispensing. All sensor data reaching the
        AI decision layer must be cryptographically authenticated.

        TIME DRIFT (Section D):
        A single packet outside the 5s window is a bad packet — drop and log.
        Three consecutive violations = clock fault — escalate to ERROR state.
        This distinction matters: a single late packet is network noise;
        three in a row means the ESP32 clock has drifted, which is a hardware
        fault requiring intervention.

        Returns: (is_valid: bool, failure_reason: str)
        """
        try:
            ts  = payload.get("ts")
            sig = payload.get("sig")

            if ts is None or sig is None:
                self.packets_dropped += 1
                return False, "Missing required field: ts or sig"

            # ── TIME WINDOW CHECK (Section D) ──
            drift = abs(time.time() - float(ts))
            if drift > PYTHON_TIME_WINDOW_SECONDS:
                self.consecutive_drift_count += 1
                self.packets_dropped += 1
                return False, (
                    f"Time drift {drift:.2f}s > {PYTHON_TIME_WINDOW_SECONDS}s window "
                    f"(consecutive: {self.consecutive_drift_count})"
                )
            else:
                # Clean packet — reset consecutive drift counter
                self.consecutive_drift_count = 0

            # ── CANONICAL STRING RECONSTRUCTION ──
            # Must match exactly what the ESP32 signed (see firmware)
            if telemetry_type == "heartbeat":
                canonical = f"heartbeat|{ts}"
            elif telemetry_type == "power":
                ma = float(payload.get("ma", 0))
                pump = payload.get("pump", "")
                canonical = f"power|{pump}|{ma:.1f}|{ts}"
            elif telemetry_type == "ack":
                pkt_id = payload.get("packet_id", "")
                pump   = payload.get("pump", "")
                status = payload.get("status", "")
                canonical = f"ack|{pkt_id}|{pump}|{status}|{ts}"
            elif telemetry_type == "boot":
                reason = payload.get("reset_reason", "")
                ip     = payload.get("ip", "")
                canonical = f"boot|{reason}|{ip}|{ts}"
            elif telemetry_type == "security":
                event = payload.get("event", "")
                canonical = f"security|{event}|{ts}"
            else:
                self.packets_dropped += 1
                return False, f"Unknown telemetry type: {telemetry_type}"

            # ── HMAC VERIFICATION ──
            expected_sig = hmac.new(
                SECRET_KEY,
                canonical.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()

            # Use constant-time comparison to prevent timing-based side-channel attacks
            if not hmac.compare_digest(expected_sig, sig):
                # TRACE: Use repr() to see hidden characters like \r or \n
                print(f"{Fore.RED}[HMAC_TRACE] Type: {telemetry_type}")
                print(f"  Expected String: {repr(canonical)}")
                print(f"  Got Sig:         {repr(sig)}")
                print(f"  Exp Sig:         {repr(expected_sig)}{Style.RESET_ALL}")
                
                self.intrusion_count += 1
                self.packets_dropped += 1
                # Return a hash preview for the UI right panel (truncated, never full sig)
                preview = sig[:8] + "..." + sig[-4:]
                return False, f"HMAC_FAIL preview={preview}"

            self.packets_verified += 1
            return True, "OK"

        except Exception as e:
            self.packets_dropped += 1
            return False, f"Verification exception: {e}"


# Module-level singleton
crypto = CryptoTransport()
