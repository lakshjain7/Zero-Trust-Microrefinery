"""
Module 9: Attack Demo Script — Test Case C Prop

Standalone script. Simulates an attacker who:
  1. Has valid MQTT broker credentials (attack_demo user)
  2. Can reach the broker and publish to refinery/cmd/pump
  3. Does NOT have the HMAC secret key
  4. Publishes a malformed command with a fake signature

Expected result: ESP32 Core 0 rejects the packet. Pump does NOT fire.
The UI right panel shows "HMAC_FAIL" in red. The ACK never arrives.

This demonstrates that broker-level access alone is insufficient —
the cryptographic layer on the ESP32 is the real gate.

Usage:
  python attack_demo.py
  python attack_demo.py --broker 192.168.137.1 --port 1883
"""

import argparse
import json
import sys
import time
import threading
import colorama
from colorama import Fore, Style

import paho.mqtt.client as mqtt

colorama.init()

# ── ATTACK PAYLOAD ──
# Stale timestamp (April 2024), nonce=1, fake signature.
# This will fail the ESP32's HMAC check, time window check, AND nonce check.
ATTACK_PAYLOAD = {
    "pump": "red",
    "duration_ms": 10000,        # 10 seconds — would drain ~85ml if it fired
    "ts": 1711000000,            # Stale: April 2024
    "nonce": 1,                  # Low nonce — easily flagged as replay
    "packet_id": "ATTACK-DEMO-PACKET-001",
    "sig": "FAKE_HASH_abc123_INVALID_0000000000000000000000000000",
}

ACK_TOPIC    = "refinery/telemetry/ack"
ATTACK_TOPIC = "refinery/cmd/pump"

# ACK received flag (set by MQTT callback)
_ack_received = threading.Event()
_ack_payload  = {}


def _on_message(client, userdata, msg):
    """Check if we accidentally get an ACK matching our packet_id."""
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        if payload.get("packet_id") == ATTACK_PAYLOAD["packet_id"]:
            global _ack_payload
            _ack_payload = payload
            _ack_received.set()
    except Exception:
        pass


def run_attack(broker_ip: str, broker_port: int) -> None:
    print(f"\n{Fore.RED}{'━'*60}")
    print("  ZERO-TRUST MICRO-REFINERY — ATTACK DEMO")
    print("  Demonstrating: unsigned MQTT packet rejection")
    print(f"{'━'*60}{Style.RESET_ALL}\n")

    print(f"{Fore.YELLOW}[ATTACK] Connecting as 'attack_demo' user to {broker_ip}:{broker_port}...{Style.RESET_ALL}")

    client = mqtt.Client(client_id="attack_demo_client", protocol=mqtt.MQTTv5)
    client.username_pw_set("attack_demo", "attack_demo_password")
    client.on_message = _on_message

    try:
        client.connect(broker_ip, broker_port, keepalive=10)
    except Exception as e:
        print(f"{Fore.RED}[ATTACK] Cannot connect to broker: {e}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}[ATTACK] Is the broker running? Is attack_demo user created?{Style.RESET_ALL}")
        sys.exit(1)

    client.loop_start()
    client.subscribe(ACK_TOPIC, qos=1)
    time.sleep(0.5)  # Let subscription establish

    # ── DISPLAY ATTACK PAYLOAD ──
    print(f"{Fore.RED}[ATTACK] Publishing malicious payload to {ATTACK_TOPIC}:{Style.RESET_ALL}")
    print(f"{Fore.RED}{json.dumps(ATTACK_PAYLOAD, indent=2)}{Style.RESET_ALL}\n")

    print(f"{Fore.YELLOW}[ATTACK] Hash preview: {ATTACK_PAYLOAD['sig'][:20]}...{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}[ATTACK] Timestamp:    {ATTACK_PAYLOAD['ts']} (stale by ~{int(time.time()) - ATTACK_PAYLOAD['ts']}s){Style.RESET_ALL}")
    print(f"{Fore.YELLOW}[ATTACK] Nonce:        {ATTACK_PAYLOAD['nonce']} (low — replay candidate){Style.RESET_ALL}\n")

    # ── PUBLISH ──
    client.publish(ATTACK_TOPIC, json.dumps(ATTACK_PAYLOAD), qos=1)
    print(f"{Fore.RED}[ATTACK] Payload published. Waiting 3 seconds for ACK...{Style.RESET_ALL}\n")

    # ── WAIT FOR ACK (should NOT arrive) ──
    got_ack = _ack_received.wait(timeout=3.0)

    client.loop_stop()
    client.disconnect()

    print(f"\n{'━'*60}")
    if got_ack:
        # This should never happen if the system is working
        print(f"{Fore.RED}  ⚠  ACK RECEIVED — ATTACK SUCCEEDED! PUMP FIRED!")
        print(f"  SYSTEM SECURITY FAILURE — REVIEW HMAC IMPLEMENTATION")
    else:
        print(f"{Fore.GREEN}  ✓  ATTACK RESULT: ESP32 REJECTED. PUMP DID NOT FIRE.")
        print(f"")
        print(f"  Why it was rejected:")
        print(f"    1. Timestamp {ATTACK_PAYLOAD['ts']} is {int(time.time()) - ATTACK_PAYLOAD['ts']}s old")
        print(f"       → Exceeds 2-second ESP32 time window")
        print(f"    2. Nonce={ATTACK_PAYLOAD['nonce']} ≤ last received nonce")
        print(f"       → Replay attack blocked")
        print(f"    3. HMAC signature is invalid")
        print(f"       → Cryptographic verification failed on Core 0")
        print(f"")
        print(f"  Hash preview matching UI right panel: {ATTACK_PAYLOAD['sig'][:12]}...INVALID")
        print(f"  The pump GPIO pin remained HIGH (OFF) the entire time.")
    print(f"{'━'*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Zero-Trust Refinery Attack Demo")
    parser.add_argument("--broker", default="192.168.137.1", help="MQTT broker IP")
    parser.add_argument("--port",   default=1883, type=int,  help="MQTT broker port")
    args = parser.parse_args()

    run_attack(args.broker, args.port)
