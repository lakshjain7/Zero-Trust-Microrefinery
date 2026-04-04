"""
Zero-Trust Micro-Refinery — Entry Point

Starts all system components in order:
  1. Load environment variables
  2. Initialize SQLite audit log
  3. Crash recovery check
  4. Start FastAPI WebSocket server (background thread)
  5. Connect MQTT broker
  6. Arm Dead Man's Switch
  7. Start interactive command loop

Usage:
  python main.py            # Normal mode (requires hardware + broker)
  python main.py --mock     # Mock mode (synthetic telemetry, no hardware)

The --mock flag sets MOCK_MODE=true, which activates the synthetic
telemetry generator in websocket_server.py and simulates pump actuations.
"""

import asyncio
import os
import sys
import threading
import colorama
import logging

# Silence noisy asyncio pipe errors on Windows
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
from colorama import Fore, Style
from dotenv import load_dotenv

try:
    colorama.just_fix_windows_console()
except AttributeError:
    pass # Older colorama version without native fix

# ── LOAD ENVIRONMENT VARIABLES ──
load_dotenv()

# Process --mock flag before any module imports that read MOCK_MODE
if "--mock" in sys.argv:
    os.environ["MOCK_MODE"] = "true"

# ── DEFERRED IMPORTS (after env is set) ──
import audit_db
from system_state import shared, State
from crypto_transport import crypto
from mqtt_client import MQTTManager
from dead_mans_switch import DeadMansSwitch
from safety_governor import SafetyGovernor
from orchestrator import Orchestrator
import websocket_server


BANNER = (
    f"\n{Fore.CYAN}"
    "============================================================\n"
    "   ZERO-TRUST AGENTIC MICRO-REFINERY  v3.1\n"
    "   HMAC-SHA256 | FreeRTOS Dual-Core | LangGraph GPT-4o\n"
    "============================================================"
    f"{Style.RESET_ALL}\n"
)


def start_ws_server() -> None:
    """Start FastAPI WebSocket server in a background daemon thread."""
    websocket_server.start_server(host="0.0.0.0", port=8000)


async def main_async() -> None:
    print(BANNER)

    mock_mode = os.getenv("MOCK_MODE", "false").lower() == "true"
    if mock_mode:
        print(f"{Fore.YELLOW}⚠  MOCK MODE ACTIVE — synthetic telemetry, no hardware{Style.RESET_ALL}")

    # ── STEP 1: Initialize SQLite audit log ──
    sys.stdout.write("[INIT] Initializing audit database...\n")
    sys.stdout.flush()
    await audit_db.init_db()

    # Register state-transition callback so every transition auto-logs
    from audit_db import on_state_transition
    shared.register_transition_callback(on_state_transition)

    # ── STEP 2: Crash recovery check ──
    last_state = await audit_db.check_crash_recovery()
    if last_state:
        print(
            f"{Fore.RED}[RECOVERY] Last state was '{last_state}' — "
            f"crash detected. Physical state unknown.{Style.RESET_ALL}"
        )
        print(f"{Fore.RED}[RECOVERY] Entering ERROR state. Manual reset required.{Style.RESET_ALL}")
        # ws_broadcast not yet set — transition silently, WS will push current state on connect
        shared._state = State.ERROR
        await audit_db.log_connectivity_event(
            "CRASH_RECOVERY",
            f"Process crashed during {last_state}. Physical pump state unknown."
        )

    # ── STEP 3: Start WebSocket server (background thread) ──
    sys.stdout.write("[INIT] Starting WebSocket server on ws://localhost:8000/ws\n")
    sys.stdout.flush()
    
    # Bridge: Create a thread-safe callback that hops from this Loop (Main)
    # back to the Uvicorn Loop (WSServer thread).
    from websocket_server import thread_safe_broadcast
    shared.ws_broadcast = thread_safe_broadcast
    
    ws_thread = threading.Thread(target=start_ws_server, daemon=True, name="WSServer")
    ws_thread.start()
    await asyncio.sleep(0.5)  # Let FastAPI bind the port

    # ── STEP 4: Connect MQTT (skip in mock mode) ──
    loop = asyncio.get_event_loop()
    mqtt_mgr = MQTTManager(loop)
    gov = SafetyGovernor(mqtt_mgr)  # Initialize Safety Governor early
    websocket_server.set_governor(gov) # Inject into WS server for Direct Bypass

    if not mock_mode:
        sys.stdout.write(f"[INIT] Connecting to MQTT broker {os.getenv('MQTT_BROKER_IP', '192.168.137.1')}:1883...\n")
        sys.stdout.flush()
        try:
            mqtt_mgr.connect()
            # PURE ASYNC: Run the MQTT loop as a task on the main loop.
            # This prevents deadlocks and "coroutine never awaited" warnings.
            asyncio.create_task(mqtt_mgr.run())
            
            await asyncio.sleep(2.0)  # Wait for on_connect to fire
            if not shared.mqtt_connected:
                print(f"{Fore.RED}[INIT] MQTT connection failed. Check broker and credentials.{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}[INIT] Tip: run with --mock for demo without hardware.{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.RED}[INIT] MQTT connect exception: {e}{Style.RESET_ALL}")
    else:
        shared.mqtt_connected = True  # Mock: pretend connected

    # ── STEP 5: Arm Dead Man's Switch ──
    sys.stdout.write("[BOOT] Step 5: Arming Dead Man's Switch...\n")
    sys.stdout.flush()
    dms = DeadMansSwitch(mqtt_mgr, loop)
    dms.arm()
    sys.stdout.write("[BOOT] Step 5: SUCCESS (DMS armed)\n")
    sys.stdout.flush()

    # ── STEP 6: Create orchestrator ──
    sys.stdout.write("[BOOT] Step 6: Initializing AI Orchestrator (LangChain/OpenAI)...\n")
    sys.stdout.write("[BOOT] NOTE: This may take a moment on some systems...\n")
    sys.stdout.flush()
    
    try:
        orch = Orchestrator(mqtt_mgr, loop, gov) # Pass gov to orch too
        sys.stdout.write("[BOOT] Step 6: SUCCESS (Orchestrator ready)\n")
        sys.stdout.flush()
    except Exception as e:
        sys.stdout.write(f"\n[CRITICAL ERROR] Orchestrator failed to initialize: {e}\n")
        sys.stdout.write("[CRITICAL ERROR] Continuing without AI commands. Only UI dashboard available.\n")
        sys.stdout.flush()
        orch = None

    # Expose orchestrator and THIS MAIN LOOP to the WebSocket server
    if orch:
        websocket_server.set_orchestrator(orch, asyncio.get_running_loop())

    sys.stdout.write("\n" + "="*50 + "\n")
    sys.stdout.write("[READY] ZERO-TRUST REFINERY ONLINE\n")
    sys.stdout.write(f"[READY] Dashboard: http://localhost:8000\n")
    sys.stdout.write("="*50 + "\n\n")
    sys.stdout.flush()

    def hide_proactor_errors(loop, context):
        if "ProactorBaseWritePipeTransport" in str(context.get("message", "")) or "f is self._write_fut" in str(context.get("exception", "")):
            return
        loop.default_exception_handler(context)
    
    asyncio.get_running_loop().set_exception_handler(hide_proactor_errors)

    # ── STEP 7: Keep alive ──
    await _command_loop(orch)


async def _command_loop(orch: "Orchestrator") -> None:
    """Keep the main thread alive. Use the web dashboard for commands!"""
    while True:
        try:
            await asyncio.sleep(1.0)
        except (asyncio.CancelledError, EOFError):
            # Background Windows pipe noise — NOT a shutdown. Keep running.
            continue
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}[SHUTDOWN] Goodbye.{Style.RESET_ALL}")
            break

        # All commands are handled via the web dashboard (http://localhost:8000)
        pass


def _print_status() -> None:
    from system_state import shared
    from crypto_transport import crypto
    print(
        f"\n{Fore.CYAN}── STATUS ──────────────────────────────{Style.RESET_ALL}\n"
        f"  State         : {shared.state}\n"
        f"  MQTT connected: {shared.mqtt_connected}\n"
        f"  Exec blocked  : {shared.execution_blocked}\n"
        f"  Intrusions    : {crypto.intrusion_count}\n"
        f"  Drift count   : {crypto.consecutive_drift_count}\n"
        f"  Verified pkts : {crypto.packets_verified}\n"
        f"  Dropped pkts  : {crypto.packets_dropped}\n"
        f"  Peak mA       : {crypto.peak_ma:.1f}\n"
    )


if __name__ == "__main__":
    asyncio.run(main_async())
