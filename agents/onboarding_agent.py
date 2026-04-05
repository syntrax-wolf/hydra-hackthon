import json
import re
import logging
import time
import httpx
from datetime import date

from core.config import config
from agents.prompts import ONBOARDING_EXTRACT_PROMPT

log = logging.getLogger("onboarding_agent")

MAX_RETRIES = 2  # Max retry attempts for LLM calls (total attempts = MAX_RETRIES + 1)
REQUIRED_FIELDS = ["employee_name"]


async def _call_llm(system_prompt: str, user_message: str, label: str = "ONBOARD-LLM") -> str:
    """Call OpenRouter — same pattern as finance_agent._call_llm."""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            log.info("[%s] Calling OpenRouter (model=%s, attempt=%d/%d)...", label, config.openrouter_model, attempt + 1, MAX_RETRIES + 1)
            t0 = time.time()
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.openrouter_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": 4096,
                        "temperature": 0.1,
                        "top_p": 0.95,
                        "reasoning": {"effort": "none"},
                    },
                )
                elapsed = time.time() - t0
                response.raise_for_status()
                data = response.json()

                usage = data.get("usage", {})
                log.info("[%s] Response in %.1fs — tokens: %s", label, elapsed, usage.get("total_tokens", "?"))

                content = data["choices"][0]["message"]["content"]
                # Strip <think> blocks
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                # Strip markdown fences
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                # Strip pre-JSON text
                content = content.strip()
                if content and not content.startswith("{"):
                    idx = content.find("{")
                    if idx > 0:
                        content = content[idx:]
                return content.strip()
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_error = e
            log.warning("[%s] Attempt %d failed: %s", label, attempt + 1, e)
            if attempt < MAX_RETRIES:
                import asyncio
                await asyncio.sleep(2)
    raise last_error


async def extract_employee_info(message: str) -> dict:
    """Extract employee onboarding details from the manager's message using LLM."""
    current_date = date.today().isoformat()
    system_prompt = ONBOARDING_EXTRACT_PROMPT.format(current_date=current_date)

    raw = await _call_llm(system_prompt, message, label="EXTRACT")

    try:
        info = json.loads(raw)
        log.info("[EXTRACT] Parsed info: %s", json.dumps(info, default=str))
        return info
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                info = json.loads(match.group())
                log.info("[EXTRACT] Parsed info (extracted): %s", json.dumps(info, default=str))
                return info
            except json.JSONDecodeError:
                pass
        log.error("[EXTRACT] Failed to parse JSON from response: %s", raw[:200])
        raise ValueError("Failed to extract employee info from LLM response")


def check_missing_fields(info: dict) -> list[str]:
    """Check which required fields are missing or null."""
    missing = []
    for field in REQUIRED_FIELDS:
        val = info.get(field)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            missing.append(field)
    return missing
