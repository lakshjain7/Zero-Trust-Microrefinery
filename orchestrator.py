"""
Module 3: Python Orchestrator — Agentic AI Core

LangChain/LangGraph autonomous orchestrator with physics-injected system prompt.
Zero hardcoded if/else color logic — all mixing decisions emerge from
the LLM reasoning over the injected physical constants.

Updated for LangChain 1.x / LangGraph 1.x API:
  - Uses langgraph.prebuilt.create_react_agent (replaces deprecated AgentExecutor)
  - Uses langchain_core.tools.tool / ToolException
  - Uses langchain_core.callbacks.base.BaseCallbackHandler
  - Streaming via agent.stream() with callback config

Tools exposed to the LLM:
  - check_receptacle_state()   → HC-SR04 EMA state
  - get_current_power_draw()   → INA219 mA reading
  - actuate_pump(pump, ms)     → signed MQTT command via Safety Governor
  - alert_human(message)       → PAUSED state + autonomous resume monitor

AI Reasoning Stream:
  BaseCallbackHandler intercepts every tool call and LLM step in real
  time. Each step is pushed to the WebSocket reasoning_stream channel,
  driving the LEFT panel live stream.

PLAN VERSIONING:
  Each invocation gets a UUID4 plan_id. All commands and reasoning steps
  are logged under this plan_id in SQLite. The UI displays "Plan: a3f9c12d"
  in the LEFT panel header.
"""

import asyncio
import json
import os
import sys
import time
from uuid import uuid4
from typing import Any, Optional

import colorama
from colorama import Fore, Style

from langchain_core.tools import tool, ToolException
from langchain_core.callbacks.base import AsyncCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from system_state import shared, State
from safety_governor import SafetyGovernor, FLOW_RATES
from audit_db import log_ai_reasoning, log_connectivity_event
from crypto_transport import crypto

colorama.init()

# ── PHYSICAL CONSTANTS (Section A) ──
YELLOW_FLOW = float(os.getenv("YELLOW_FLOW_RATE_ML_PER_SEC", "8.8"))

# Injected verbatim per the directive. Physics are ground truth;
# the LLM must reason within these constraints, not around them.
SYSTEM_PROMPT = f"""You are an autonomous industrial orchestrator controlling a
Zero-Trust Micro-Refinery. You have exclusive control over 3 physical
pumps via cryptographically secured MQTT commands.

PHYSICAL CONSTANTS (real hardware — do not estimate):
  Pump: red    | Flow rate: 8.5 ml/sec  | Current: ~200mA
  Pump: blue   | Flow rate: 9.1 ml/sec  | Current: ~200mA
  Pump: yellow | Flow rate: {YELLOW_FLOW} ml/sec | Current: ~200mA

VOLUME CALCULATION:
  duration_ms = (volume_ml / flow_rate_ml_per_sec) * 1000
  Always round to nearest integer millisecond.

COLOR MIXING (physical reality):
  Purple = Red + Blue   | Green = Blue + Yellow
  Orange = Red + Yellow | Brown = Red + Blue + Yellow

POWER CONSTRAINTS (Deterministic Enforcement):
  Single pump: ~200mA. Two pumps parallel: ~400mA.
  User-defined milliampere constraint: 300mA (Hard Cap).
  Because 400mA > 300mA, you MUST always execute multi-pump mixes
  sequentially. 
  
  CRITICAL: You must AWAIT the completion of the first pump before 
  starting the next. Do not call multiple actuate_pump tools in 
  the same thought/step.

MANDATORY PRE-EXECUTION CHECKLIST (every time, no exceptions):
  1. Call check_hardware_health() to ensure pumps are ONLINE.
  2. Call get_current_power_draw() to confirm baseline.
  3. Calculate duration_ms for each pump.
  4. Verify: each duration_ms < 4000ms (Physical Safety Cap).
  5. Execute sequentially to remain under the 300mA budget.

If check_hardware_health() reports a pump is OFFLINE, abort the mix 
immediately and report the hardware failure. Do not retry.

MANDATORY: Before calling any actuate_pump tool, you MUST output a 
'PIPELINE PLAN' as a bulleted list for the user, showing:
  - Phase 1: [Pump Name] - [Volume] - [Duration]
  - Phase 2: [Pump Name] - [Volume] - [Duration]
Wait for the user to see this plan in the reasoning stream.

Physical safety supersedes efficiency. Always.
"""


# ── REAL-TIME REASONING CALLBACK HANDLER ──

class ReasoningStreamHandler(AsyncCallbackHandler):
    """
    Intercepts every LangGraph/LangChain event and pushes it to the WebSocket
    reasoning_stream channel in real time. This drives the LEFT panel
    live reasoning stream entirely — no polling, push-only.

    Security rationale: the callback is read-only — it observes decisions
    but cannot influence them. The Safety Governor remains the sole authority
    over hardware actuation decisions.
    """

    def __init__(self, session_id: str, plan_id: str, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.session_id = session_id
        self.plan_id    = plan_id
        self._loop      = loop
        self._step_idx  = 0

    async def _push(self, text: str, tool_call: Optional[str] = None, active: bool = True) -> None:
        self._step_idx += 1
        step = f"step_{self._step_idx}"

        payload = {
            "channel": "reasoning_stream",
            "plan_id": self.plan_id,
            "step":    step,
            "text":    text,
            "tool_call": tool_call,
            "active":  active,
        }
        await shared.push(payload)
        await log_ai_reasoning(
                self.session_id, self.plan_id, self._step_idx,
                step, text, tool_call or ""
            )

    # ── LLM events ──
    async def on_chat_model_start(self, serialized: dict, messages: list, **kwargs) -> None:
        await self._push("AI reasoning...", active=True)

    async def on_llm_end(self, response: Any, **kwargs) -> None:
        try:
            text = response.generations[0][0].text
            # Extract Thought line for display
            for line in text.splitlines():
                if line.strip().startswith("Thought:"):
                    await self._push(line.strip()[8:].strip(), active=True)
                    return
            if text.strip():
                await self._push(text.strip()[:300], active=False)
        except Exception:
            pass

    # ── Tool events ──
    async def on_tool_start(self, serialized: dict, input_str: str, **kwargs) -> None:
        name = serialized.get("name", "tool")
        label = f"{name}({input_str})"
        await self._push(f"Calling: {label}", tool_call=label, active=True)

    async def on_tool_end(self, output: str, **kwargs) -> None:
        await self._push(f"→ {str(output)[:200]}", active=False)

    async def on_tool_error(self, error: Exception, **kwargs) -> None:
        await self._push(f"⚠ Tool error: {str(error)}", active=False)

    async def on_llm_error(self, error: Exception, **kwargs) -> None:
        await self._push(f"⚠ LLM error: {str(error)}", active=False)


# ── ORCHESTRATOR CLASS ──

class Orchestrator:
    def __init__(self, mqtt_manager, loop: asyncio.AbstractEventLoop, governor: SafetyGovernor):
        self._mqtt       = mqtt_manager
        self._loop       = loop
        self._governor   = governor
        self._session_id = str(uuid4())
        self._failed_nodes = set()  # Track node failures to prevent infinite AI retries

        # LLM — temperature=0 for deterministic, physics-constrained reasoning
        self._llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            streaming=True,
            openai_api_key=os.getenv("OPENAI_API_KEY"),
        )

        # Build tools bound to this instance's governor and loop
        self._tools = self._build_tools()

        # LangGraph ReAct agent — system_prompt injects physics as ground truth
        self._agent = create_react_agent(
            self._llm,
            self._tools,
            prompt=SYSTEM_PROMPT,
        )

    def _build_tools(self):
        governor = self._governor
        loop     = self._loop

        @tool
        async def get_current_power_draw() -> str:
            """
            Read the last verified INA219 current measurement.
            """
            if shared.last_power is None:
                raise ToolException("No power telemetry received yet.")
            age = time.time() - shared.last_power.get("ts", 0)
            if age > 2.0:
                raise ToolException(f"Power reading stale ({age:.1f}s > 2s).")
            return json.dumps({
                "ma":        shared.last_power.get("ma", 0.0),
                "timestamp": shared.last_power.get("ts", 0),
                "age_ms":    int(age * 1000),
            })

        @tool
        async def actuate_pump(pump_id: str, duration_ms: int) -> str:
            """
            Fire a physical pump for the specified duration in milliseconds.
            pump_id must be exactly 'red', 'blue', or 'yellow'.
            duration_ms is calculated from: (volume_ml / flow_rate) * 1000.
            Returns JSON with status, packet_id, actual_duration_ms.
            """
            # ── HARDWARE LOCKOUT ──
            if pump_id in self._failed_nodes:
                return json.dumps({
                    "status": "LOCKOUT",
                    "reason": f"Hardware {pump_id} previously failed. Check wiring/broker."
                })

            if pump_id not in FLOW_RATES:
                raise ToolException(
                    f"Invalid pump_id: '{pump_id}'. Must be exactly red, blue, or yellow."
                )

            # Await the governor directly on the main loop — NO DEADLOCK!
            try:
                result = await governor.approve_and_execute(pump_id, int(duration_ms))
            except Exception as e:
                raise ToolException(f"Execution exception: {e}")

            if result["status"] == "REJECTED":
                raise ToolException(f"GOVERNOR REJECTED: {result['reason']}")

            if result["status"] == "ERROR":
                # Mark as faulty to prevent AI retry loops during this session
                self._failed_nodes.add(pump_id)
                # We return the error rather than raising Exception so the AI 
                # can gracefully report the lockout to the user.
                return json.dumps(result)

            return json.dumps(result)

        @tool
        async def check_hardware_health() -> str:
            """
            Check the real-time online/offline status of all 3 pumps.
            Always call this before starting any mixing operation.
            """
            health = {
                id: ("ONLINE" if shared.is_node_online(id) else "OFFLINE")
                for id in ["red", "blue", "yellow"]
            }
            return json.dumps(health)

        return [get_current_power_draw, actuate_pump, check_hardware_health]

    async def run(self, command: str) -> str:
        """
        Execute a natural-language command through the LangGraph ReAct agent.
        Generates a plan_id, streams reasoning to UI via callbacks, runs agent.
        """
        plan_id    = str(uuid4())
        short_plan = plan_id[:8]

        sys.stdout.write(f"\n[ORCH] >>> Entering run() for: '{command}'\n")
        sys.stdout.write(f"[ORCH] Plan ID: {short_plan}\n")
        sys.stdout.flush()

        # Transition to EXECUTING and focus the LEFT (AI) panel
        sys.stdout.write("[ORCH] Transitioning to EXECUTING...\n")
        sys.stdout.flush()
        await shared.transition(
            State.EXECUTING,
            trigger=f"PLAN_{short_plan}",
            direction="forward",
            focus="LEFT",
        )

        # Push plan_id so the LEFT panel header shows "Plan: a3f9c12d"
        await shared.push({
            "channel":   "reasoning_stream",
            "plan_id":   short_plan,
            "step":      "plan_start",
            "text":      f"Starting plan {short_plan}: {command}",
            "tool_call": None,
            "active":    True,
        })

        sys.stdout.write("[ORCH] Handing off to AI Agent (LangGraph)...\n")
        sys.stdout.flush()
        handler = ReasoningStreamHandler(self._session_id, short_plan, self._loop)
        output  = "Completed."

        try:
            # PURE ASYNC: Use ainvoke directly on the main loop.
            # This ensures that the AsyncCallbackHandler and async Tools
            # all share the same event loop concurrency context,
            # preventing deadlocks on Windows (Python 3.14).
            result = await self._agent.ainvoke(
                {"messages": [HumanMessage(content=command)]},
                config={"callbacks": [handler]},
            )
            # Extract final message content from LangGraph result
            messages = result.get("messages", [])
            if messages:
                last = messages[-1]
                output = getattr(last, "content", str(last))
                if isinstance(output, list):
                    # Some models return list of content blocks
                    output = " ".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in output
                    )

        except Exception as e:
            output = f"Agent error: {e}"
            print(f"{Fore.RED}[ORCH] Agent exception: {e}{Style.RESET_ALL}")

        # Return to IDLE if still in EXECUTING (not PAUSED waiting for human)
        if shared.state == State.EXECUTING:
            await shared.transition(
                State.IDLE,
                trigger=f"PLAN_{short_plan}_COMPLETE",
                direction="none",
                focus="NONE",
            )

        await shared.push({
            "channel":   "reasoning_stream",
            "plan_id":   short_plan,
            "step":      "plan_complete",
            "text":      f"✓ {output[:300]}",
            "tool_call": None,
            "active":    False,
        })

        print(f"{Fore.GREEN}[ORCH] Plan {short_plan} complete: {output[:100]}{Style.RESET_ALL}")
        return output
