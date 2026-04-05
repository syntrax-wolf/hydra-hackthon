import json
import re
import logging
import time
import httpx
from datetime import date
from core.config import config
from agents.prompts import DECOMPOSE_SYSTEM_PROMPT, ANALYZE_SYSTEM_PROMPT

log = logging.getLogger("finance_agent")

MAX_RETRIES = 2  # Max retry attempts for LLM calls (total attempts = MAX_RETRIES + 1)


async def _call_llm(system_prompt: str, messages: list[dict], label: str = "LLM") -> str:
    """Call OpenRouter with a system prompt and a list of messages (for conversation context)."""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            log.info("[%s] Calling OpenRouter (model=%s, attempt=%d/%d)...", label, config.openrouter_model, attempt + 1, MAX_RETRIES + 1)
            t0 = time.time()
            all_messages = [{"role": "system", "content": system_prompt}] + messages
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.openrouter_model,
                        "messages": all_messages,
                        "max_tokens": 16384,
                        "temperature": 0.1,
                        "top_p": 0.95,
                        "reasoning": {"effort": "none"},
                    },
                )
                elapsed = time.time() - t0
                response.raise_for_status()
                data = response.json()

                usage = data.get("usage", {})
                log.info("[%s] Response received in %.1fs — tokens: prompt=%s, completion=%s, total=%s",
                         label, elapsed,
                         usage.get("prompt_tokens", "?"),
                         usage.get("completion_tokens", "?"),
                         usage.get("total_tokens", "?"))

                content = data["choices"][0]["message"]["content"]

                # Strip <think>...</think> blocks (Qwen3 thinking mode)
                think_match = re.search(r"<think>", content)
                if think_match:
                    log.info("[%s] Stripping <think> block from response", label)
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

                # Strip markdown code fences
                if content.startswith("```"):
                    log.info("[%s] Stripping markdown code fences", label)
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]

                # Strip any pre-JSON text (safety net for untagged reasoning)
                content = content.strip()
                if content and not content.startswith("{"):
                    json_start = content.find("{")
                    if json_start > 0:
                        log.info("[%s] Stripping %d chars of pre-JSON text", label, json_start)
                        content = content[json_start:]

                cleaned = content.strip()
                log.info("[%s] Cleaned response length: %d chars", label, len(cleaned))
                return cleaned
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_error = e
            log.warning("[%s] Attempt %d failed: %s", label, attempt + 1, e)
            if attempt < MAX_RETRIES:
                import asyncio
                log.info("[%s] Retrying in 2s...", label)
                await asyncio.sleep(2)
    log.error("[%s] All %d attempts failed", label, MAX_RETRIES + 1)
    raise last_error


def _parse_json(raw: str, label: str = "LLM") -> dict:
    try:
        result = json.loads(raw)
        log.info("[%s] JSON parsed successfully (direct)", label)
        return result
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            log.info("[%s] JSON parsed successfully (extracted from response)", label)
            return result
        except json.JSONDecodeError:
            pass
    log.error("[%s] Failed to parse JSON. Raw response preview: %s", label, raw[:200])
    raise ValueError(f"Failed to parse JSON from LLM response: {raw[:300]}")


def _build_conversation_context(conversation_history: list[dict]) -> str:
    """Build a conversation context string from history."""
    if not conversation_history:
        return ""
    lines = ["Previous conversation:"]
    # Keep only the last 5 turns to avoid token bloat
    recent = conversation_history[-10:]
    for turn in recent:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role == "user":
            lines.append(f"Manager asked: {content}")
        else:
            # Truncate long assistant responses
            lines.append(f"Analysis showed: {content[:300]}")
    return "\n".join(lines)


async def decompose(query: str, output_format: str, conversation_history: list[dict] = None) -> dict:
    log.info("=" * 60)
    log.info("[DECOMPOSE] Starting query decomposition")
    log.info("[DECOMPOSE] Query: %s", query)
    log.info("[DECOMPOSE] Format: %s", output_format)
    if conversation_history:
        log.info("[DECOMPOSE] Conversation history: %d turns", len(conversation_history))

    current_date = date.today().isoformat()
    system_prompt = DECOMPOSE_SYSTEM_PROMPT.format(current_date=current_date)

    # Build messages with conversation context
    messages = []
    context = _build_conversation_context(conversation_history or [])
    user_content = ""
    if context:
        user_content += context + "\n\n"
    user_content += (
        f'Manager\'s question: "{query}"\n'
        f'User\'s role: Regional Manager\n'
        f'Preferred output format: {output_format}'
    )
    messages.append({"role": "user", "content": user_content})

    raw = await _call_llm(system_prompt, messages, label="DECOMPOSE")
    result = _parse_json(raw, label="DECOMPOSE")

    log.info("[DECOMPOSE] Intent: %s", result.get("intent", "unknown"))
    dr = result.get("data_requirements", [])
    log.info("[DECOMPOSE] Data requirements: %d queries planned", len(dr))
    for req in dr:
        log.info("[DECOMPOSE]   -> %s [%s] table=%s, columns=%s, filters=%s",
                 req.get("req_id"), req.get("priority", "required"),
                 req.get("table"), req.get("columns"), req.get("filters", {}))
    log.info("[DECOMPOSE] Analysis plan: %s", result.get("analysis_plan", "N/A"))
    return result


async def analyze(query: str, decomposition: dict, data_results: list, output_format: str,
                  conversation_history: list[dict] = None) -> dict:
    log.info("=" * 60)
    log.info("[ANALYZE] Starting data analysis")
    total_rows = sum(r.get("row_count", 0) for r in data_results if r.get("status") == "ok")
    log.info("[ANALYZE] Input: %d data results, %d total rows, format=%s", len(data_results), total_rows, output_format)

    system_prompt = ANALYZE_SYSTEM_PROMPT.format(output_format=output_format)

    context = _build_conversation_context(conversation_history or [])
    payload = {
        "original_query": query,
        "decomposition": decomposition,
        "retrieved_data": data_results,
        "output_format": output_format,
    }
    if context:
        payload["conversation_context"] = context

    user_message = json.dumps(payload, default=str)
    log.info("[ANALYZE] User message size: %d chars", len(user_message))

    messages = [{"role": "user", "content": user_message}]
    raw = await _call_llm(system_prompt, messages, label="ANALYZE")
    result = _parse_json(raw, label="ANALYZE")

    narrative = result.get("narrative", {})
    log.info("[ANALYZE] Executive summary: %s", (narrative.get("executive_summary", "")[:100] + "...") if narrative.get("executive_summary") else "N/A")
    log.info("[ANALYZE] Key findings: %d", len(narrative.get("key_findings", [])))
    for f in narrative.get("key_findings", []):
        log.info("[ANALYZE]   -> [%s] %s (%s)", f.get("sentiment"), f.get("finding", "")[:80], f.get("metric"))
    log.info("[ANALYZE] Recommendations: %d", len(narrative.get("recommendations", [])))
    for r in narrative.get("recommendations", []):
        log.info("[ANALYZE]   -> [%s] %s", r.get("priority"), r.get("action", "")[:80])

    ci = result.get("coding_instructions", {})
    log.info("[ANALYZE] Coding instructions: format=%s, sections=%d, title=%s",
             ci.get("output_format", "?"), len(ci.get("sections", [])), ci.get("title", "?"))

    return result
