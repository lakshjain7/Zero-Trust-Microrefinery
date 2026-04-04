"""
Module 8: WebSocket API & UI Data Bridge

FastAPI server exposing ws://localhost:8000/ws.
All WebSocket messages carry a "channel" field that the React dashboard
routes to the appropriate panel.

MOCK MODE (--mock flag passed via env MOCK_MODE=true):
  Generates synthetic telemetry so the full demo can run without
  ESP32 hardware. LLM still calls the real OpenAI API. actuate_pump()
  is simulated (no GPIO). Mock mode badge is pushed to UI on connect.
"""

import asyncio
import json
import math
import os
import time
from pathlib import Path
import sys
from typing import Optional, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

# Deferred import — system_state is available at module level after main.py runs
from system_state import shared, State

app = FastAPI(title="Zero-Trust Refinery WebSocket Bridge")

# ── CONNECTED CLIENTS ──
_clients: Set[WebSocket] = set()
_clients_lock: Optional[asyncio.Lock] = None

MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"

# ── ORCHESTRATOR REFERENCE ──
# Injected by main.py after the Orchestrator is constructed so the WebSocket
# endpoint can dispatch natural-language commands from the dashboard input
# directly to the LangChain agent without requiring the CLI.
_orchestrator = None
_governor = None
_main_loop     = None  # The loop where the orchestrator lives
_uvicorn_loop  = None  # The loop where this server is running



def set_orchestrator(orch, loop: asyncio.AbstractEventLoop) -> None:
    """Called by main.py to inject the Orchestrator and its event loop."""
    global _orchestrator, _main_loop
    _orchestrator = orch
    _main_loop     = loop

def set_governor(gov) -> None:
    """Called by main.py to inject the SafetyGovernor."""
    global _governor
    _governor = gov


async def thread_safe_broadcast(payload: dict) -> None:
    """Non-blocking wrapper that schedules a broadcast on the Uvicorn loop."""
    if _uvicorn_loop:
        asyncio.run_coroutine_threadsafe(broadcast(payload), _uvicorn_loop)


async def broadcast(payload: dict) -> None:
    global _uvicorn_loop
    if _uvicorn_loop is None:
        try:
            _uvicorn_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass # Not in a loop yet
    """
    Broadcast a JSON payload to all connected WebSocket clients.
    """
    global _clients_lock
    if _clients_lock is None:
        _clients_lock = asyncio.Lock()

    msg = json.dumps(payload)
    async with _clients_lock:
        dead = set()
        for ws in _clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        _clients.difference_update(dead)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    global _uvicorn_loop, _clients_lock
    if _uvicorn_loop is None:
        _uvicorn_loop = asyncio.get_running_loop()
    if _clients_lock is None:
        _clients_lock = asyncio.Lock()

    await websocket.accept()
    sys.stdout.write(f"[WS] OPEN: New client connected. Mode={'MOCK' if MOCK_MODE else 'REAL'}\n")
    sys.stdout.flush()
    async with _clients_lock:
        _clients.add(websocket)

    # On connect: push current state and mock mode flag
    await websocket.send_text(json.dumps({
        "channel": "state_change",
        "state": shared.state,
        "direction": "none",
    }))

    if MOCK_MODE:
        await websocket.send_text(json.dumps({"channel": "mock_mode"}))

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Keepalive timeout — no message; continue listening
                continue

            # Parse and dispatch incoming messages from the dashboard
            try:
                msg = json.loads(raw)
                sys.stdout.write(f"[WS] IN: {raw}\n")
                sys.stdout.flush()
            except json.JSONDecodeError:
                continue

            if msg.get("channel") == "command":
                cmd_text = msg.get("text", "").strip()
                if cmd_text:
                    asyncio.create_task(_dispatch_command(cmd_text, websocket))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        sys.stdout.write(f"[WS] EXCEPTION in loop: {e}\n")
        sys.stdout.flush()
    finally:
        async with _clients_lock:
            _clients.discard(websocket)


async def _dispatch_command(cmd_text: str, requester: WebSocket) -> None:
    """
    Dispatch a natural-language command from the dashboard to the orchestrator.

    Security rationale: commands still pass through the full LangChain agent →
    Safety Governor → HMAC signing pipeline. The WebSocket is a UI delivery
    mechanism, not a shortcut past the security layers.
    """
    # ── DIRECT BYPASS MODE (New) ──
    # Format: direct:pumpid:ms (e.g. direct:red:2000)
    if cmd_text.startswith("direct:"):
        sys.stdout.write(f"[WS] -> DIRECT BYPASS: '{cmd_text}'\n")
        sys.stdout.flush()
        
        parts = cmd_text.split(":")
        if len(parts) == 3:
            pump_id = parts[1].strip()
            try:
                ms = int(parts[2].strip())
                if _governor and _main_loop:
                    # Hop to the main loop and call the GOVERNOR directly!
                    asyncio.run_coroutine_threadsafe(
                        _governor.approve_and_execute(pump_id, ms), 
                        _main_loop
                    )
                    sys.stdout.write(f"[WS] -> DIRECT SUCCESS (Scheduled {pump_id} at {ms}ms)\n")
                    sys.stdout.flush()
                    return
            except ValueError:
                pass
        
        sys.stdout.write(f"[WS] ERROR: Invalid direct command syntax or governor missing.\n")
        sys.stdout.flush()
        return

    # ── STANDARD AI MODE ──
    if _orchestrator is None:
        # Orchestrator not yet ready (startup race) — inform UI
        await requester.send_text(json.dumps({
            "channel": "reasoning_stream",
            "step": "error",
            "text": "⚠ Orchestrator not ready. Try again in a moment.",
            "active": False,
        }))
        return

    # Guard: reject commands while in fault states (matches CLI behavior)
    if shared.state in ("ERROR", "BREACH"):
        await requester.send_text(json.dumps({
            "channel": "reasoning_stream",
            "step": "error",
            "text": f"⛔ System in {shared.state}. Type 'reset' in CLI or use HALT button.",
            "active": False,
        }))
        return

    # Run the orchestrator on THE MAIN LOOP (not this thread's loop!)
    sys.stdout.write(f"[WS] -> Handoff to MAIN LOOP: '{cmd_text}'\n")
    sys.stdout.flush()

    try:
        if _main_loop is None:
            sys.stdout.write("[WS] ERROR: _main_loop is None, cannot dispatch!\n")
            sys.stdout.flush()
            return
            
        asyncio.run_coroutine_threadsafe(_orchestrator.run(cmd_text), _main_loop)
        sys.stdout.write("[WS] -> Handoff SUCCESS (Future scheduled)\n")
        sys.stdout.flush()
    except Exception as exc:
        sys.stdout.write(f"[WS] Execution scheduling error: {exc}\n")
        sys.stdout.flush()
        asyncio.create_task(broadcast({
            "channel": "reasoning_stream",
            "step": "error",
            "text": f"⛔ Orchestrator error: {exc}",
            "active": False,
        }))


# ── MOCK TELEMETRY GENERATOR ──

async def _mock_telemetry_loop() -> None:
    """
    Generate synthetic telemetry for demo-without-hardware mode.

    Schedule:
      Power   : sine wave 150–280mA, spike to 310mA every 30s
      Distance: EMPTY(12cm) → FULL(3cm) → NO_CUP(20cm) every 20s cycle
      Security: fake HMAC_FAIL every 45s
      Connectivity: MQTT_DISCONNECT event every 60s
      Clock drift: DRIFT_ESCALATION once at t=90s
    """
    start = time.time()
    drift_fired = False
    cycle_len = 20.0   # distance cycle length in seconds

    while True:
        await asyncio.sleep(0.5)
        t = time.time() - start

        # ── POWER (sine wave) ──
        power_ma = 215 + 65 * math.sin(t * 0.3)
        if int(t) % 30 == 0 and int(t) > 0:
            power_ma = 312  # spike over 300mA limit every 30s
        power_ma = round(max(0, min(600, power_ma)), 1)

        await broadcast({
            "channel": "telemetry_power",
            "ma": power_ma,
            "ts": int(time.time()),
        })

        # ── HEARTBEAT (synthetic) ──
        shared.last_heartbeat_time = time.time()
        await broadcast({
            "channel": "telemetry_heartbeat",
            "ts": int(time.time()),
            "sig": "MOCK_SIG",
        })

        # ── DISTANCE (state cycle) ──
        phase = t % cycle_len
        if phase < 8:
            ema_cm, cup_state = 11.5, "EMPTY_CUP"
        elif phase < 14:
            ema_cm, cup_state = 3.2, "FULL_CUP"
        else:
            ema_cm, cup_state = 19.8, "NO_CUP"

        await broadcast({
            "channel": "telemetry_distance",
            "ema_cm": ema_cm,
            "state": cup_state,
            "ts": int(time.time()),
        })

        # ── HMAC_FAIL every 45s ──
        if int(t) > 0 and int(t) % 45 == 0:
            await broadcast({
                "channel": "security_event",
                "event": "HMAC_FAIL",
                "topic": "refinery/cmd/pump",
                "hash_preview": "FAKE_ab12...INVALID",
                "timestamp": int(time.time()),
            })

        # ── MQTT DISCONNECT every 60s ──
        if int(t) > 0 and int(t) % 60 == 0:
            await broadcast({
                "channel": "connectivity_event",
                "event": "MQTT_DISCONNECT",
                "detail": "Mock broker disconnect (simulated)",
                "ts": int(time.time()),
            })
            # Simulate reconnect 3 seconds later
            await asyncio.sleep(3)
            await broadcast({
                "channel": "connectivity_event",
                "event": "MQTT_RECONNECT",
                "detail": "Mock broker reconnected",
                "ts": int(time.time()),
            })

        # ── DRIFT_ESCALATION once at t≈90s ──
        if not drift_fired and t > 90:
            drift_fired = True
            await broadcast({
                "channel": "connectivity_event",
                "event": "DRIFT_ESCALATION",
                "detail": "3 consecutive packets outside 5s window",
                "ts": int(time.time()),
            })
            await asyncio.sleep(10)
            await broadcast({
                "channel": "connectivity_event",
                "event": "CLOCK_FAULT",
                "detail": "ESP32 clock desynchronized. Awaiting NTP re-sync.",
                "ts": int(time.time()),
            })

        # ── SYSTEM METRICS (every 5s) ──
        if int(t) % 5 == 0:
            from crypto_transport import crypto
            await broadcast({
                "channel": "system_metrics",
                "packets_verified": crypto.packets_verified,
                "packets_dropped": crypto.packets_dropped,
                "uptime_seconds": int(t),
                "peak_ma": crypto.peak_ma,
            })


def start_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Called from main.py. Injects broadcast into SharedState first."""
    shared.ws_broadcast = broadcast

    # ── SERVE BUILT REACT DASHBOARD AS STATIC FILES ──
    # The dashboard is pre-built with `npm run build` inside dashboard/dist.
    # FastAPI serves it directly so only ONE server + ONE URL is needed.
    _dist = Path(__file__).parent / "dashboard" / "dist"
    if _dist.exists():
        # Serve JS/CSS/assets from /assets/
        app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

        @app.get("/favicon.svg")
        async def favicon():
            return FileResponse(str(_dist / "favicon.svg"))

        @app.get("/icons.svg")
        async def icons():
            return FileResponse(str(_dist / "icons.svg"))

        @app.get("/")
        async def serve_index():
            return FileResponse(str(_dist / "index.html"))

        # Catch-all: any unknown route returns index.html (SPA routing)
        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            index = _dist / "index.html"
            return FileResponse(str(index))

    if MOCK_MODE:
        @app.on_event("startup")
        async def _start_mock():
            asyncio.create_task(_mock_telemetry_loop())

    uvicorn.run(app, host=host, port=port, log_level="warning")
