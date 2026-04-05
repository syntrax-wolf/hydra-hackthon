import json
import re
import logging
import time
import httpx
from pathlib import Path

from core.config import config
from core.db import execute_write
from core.sandbox import execute, validate_syntax
from agents import coding_agent
from agents.prompts import ONBOARDING_DOC_PROMPT

log = logging.getLogger("onboarding.doc")

MAX_RETRIES = 2  # Max retry attempts for LLM calls (total attempts = MAX_RETRIES + 1)


async def _call_llm_json(system_prompt: str, label: str = "DOC") -> dict:
    """Call OpenRouter and return parsed JSON. Same pattern as finance_agent._call_llm."""
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
                            {"role": "user", "content": "Generate the onboarding document specification now."},
                        ],
                        "max_tokens": 8192,
                        "temperature": 0.1,
                        "top_p": 0.95,
                        "reasoning": {"effort": "none"},
                    },
                )
                elapsed = time.time() - t0
                response.raise_for_status()
                data = response.json()
                log.info("[%s] Response in %.1fs", label, elapsed)

                content = data["choices"][0]["message"]["content"]
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()
                if content and not content.startswith("{"):
                    idx = content.find("{")
                    if idx > 0:
                        content = content[idx:]
                return json.loads(content)
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_error = e
            log.warning("[%s] Attempt %d failed: %s", label, attempt + 1, e)
            if attempt < MAX_RETRIES:
                import asyncio
                await asyncio.sleep(2)
    raise last_error


async def generate_onboarding_doc(onboarding_id: int, employee_name: str, department: str,
                                   designation: str, region: str, start_date: str,
                                   manager_name: str, buddy_name: str,
                                   accounts: list[dict], meeting_time: str) -> str:
    """Generate an onboarding PDF. Returns the file path."""
    accounts_str = ", ".join(f"{a['system']}: {a['account_id']}" for a in accounts)
    prompt = ONBOARDING_DOC_PROMPT.format(
        employee_name=employee_name,
        department=department,
        designation=designation,
        region=region or "N/A",
        start_date=start_date,
        manager_name=manager_name,
        buddy_name=buddy_name or "TBD",
        accounts=accounts_str,
        meeting_time=meeting_time or "TBD",
    )

    log.info("[DOC] Generating onboarding doc spec for %s...", employee_name)
    doc_spec = await _call_llm_json(prompt, label="DOC-SPEC")

    # Build analysis-like dict to pass to coding_agent.generate()
    generated_dir = Path(config.generated_dir).resolve()
    generated_dir.mkdir(parents=True, exist_ok=True)
    filename = f"onboarding_{employee_name.lower().replace(' ', '_')}_{onboarding_id}.pdf"
    output_path = str(generated_dir / filename)

    analysis = {
        "coding_instructions": doc_spec.get("coding_instructions", doc_spec),
        "narrative": {
            "executive_summary": f"Onboarding document for {employee_name}",
        },
    }

    # Ensure output_format is pdf
    ci = analysis["coding_instructions"]
    if "output_format" not in ci:
        ci["output_format"] = "pdf"

    log.info("[DOC] Generating code for onboarding PDF...")

    try:
        code = await coding_agent.generate(analysis, output_path)

        # Validate + execute with retry (max MAX_RETRIES retries)
        for attempt in range(MAX_RETRIES + 1):
            syntax_err = validate_syntax(code)
            if syntax_err:
                log.warning("[DOC] Syntax error attempt %d/%d: %s", attempt + 1, MAX_RETRIES + 1, syntax_err[:150])
                if attempt < MAX_RETRIES:
                    code = await coding_agent.fix_code(code, syntax_err, output_path)
                    continue
                break

            try:
                from core.sandbox import execute as sandbox_execute, SandboxError
                sandbox_execute(code, output_path)
                log.info("[DOC] Onboarding PDF generated: %s", output_path)

                execute_write(
                    "UPDATE onboarding.onboarding_records SET onboarding_doc_path = %s, status = 'doc_generated', current_step = 4 WHERE onboarding_id = %s",
                    [filename, onboarding_id],
                )
                return filename
            except Exception as e:
                log.error("[DOC] Sandbox error attempt %d/%d: %s", attempt + 1, MAX_RETRIES + 1, str(e)[:200])
                if attempt < MAX_RETRIES:
                    code = await coding_agent.fix_code(code, str(e), output_path)

    except Exception as e:
        log.error("[DOC] Coder pipeline crashed for onboarding_id=%d: %s", onboarding_id, str(e)[:200])

    # If all retries fail or pipeline crashed, still update status so onboarding continues
    execute_write(
        "UPDATE onboarding.onboarding_records SET status = 'doc_generated', current_step = 4 WHERE onboarding_id = %s",
        [onboarding_id],
    )
    log.error("[DOC] Failed to generate PDF for onboarding_id=%d — continuing without document", onboarding_id)
    return None
