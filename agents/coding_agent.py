"""Coder Agent — Multi-agent code generation pipeline.

Coordinates a 3-agent coding pipeline:
1. CODER (this module) — Generates Python code from LLM
2. SYNTAX CHECKER — Reviews code for syntax errors and API misuse
3. CODE REVIEWER — Executes code, verifies output, provides critique

Pipeline flow:
  Instruction → Coder generates code
                    ↓
              Syntax Checker reviews (catches API misuse before execution)
                    ↓
              If errors found → use fixed code
                    ↓
              Code Reviewer executes and verifies
                    ↓
              If execution fails → reviewer provides fix → next cycle
                    ↓
              Return final result

Safety limits:
  - MAX_PIPELINE_CYCLES: 2 (generate → check → fix → check = 2 cycles max)
  - Sandbox timeout: 60s per execution
"""

import asyncio
import json
import re
import logging
import time
import httpx
from pathlib import Path
from core.config import config
from core.sandbox import execute_detailed, validate_syntax
from agents.prompts import CODING_SYSTEM_PROMPT
from agents import syntax_checker, code_reviewer

log = logging.getLogger("coding_agent")

MAX_PIPELINE_CYCLES = 2
MAX_RETRIES = 2  # Max retry attempts for LLM calls (total attempts = MAX_RETRIES + 1)
AGENT_TIMEOUT_SECONDS = 120  # 2-minute timeout for syntax checker and code reviewer


async def _call_llm(system_prompt: str, user_message: str, label: str = "LLM") -> str:
    last_error = None
    model = config.openrouter_coding_model
    for attempt in range(MAX_RETRIES + 1):
        try:
            log.info("[%s] Calling OpenRouter (model=%s, attempt=%d/%d)...", label, model, attempt + 1, MAX_RETRIES + 1)
            t0 = time.time()
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": 16384,
                        "temperature": 0.1,
                        "top_p": 0.95,
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

                # Strip <think>...</think> blocks
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

                # Strip any pre-code text (safety net for untagged reasoning)
                content = content.strip()
                if content and not content.startswith(("import ", "from ", "#", "def ", "class ", "\"\"\"", "'''")):
                    for marker in ["import ", "from ", "# ", "def ", "class "]:
                        idx = content.find("\n" + marker)
                        if idx >= 0:
                            log.info("[%s] Stripping %d chars of pre-code text", label, idx)
                            content = content[idx + 1:]
                            break

                cleaned = content.strip()
                log.info("[%s] Generated code length: %d chars, ~%d lines", label, len(cleaned), cleaned.count("\n") + 1)
                return cleaned
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_error = e
            log.warning("[%s] Attempt %d failed: %s", label, attempt + 1, e)
            if attempt < MAX_RETRIES:
                log.info("[%s] Retrying in 2s...", label)
                await asyncio.sleep(2)
    log.error("[%s] All %d attempts failed", label, MAX_RETRIES + 1)
    raise last_error


def _postprocess_code(code: str) -> str:
    """Fix common LLM code generation mistakes with regex before syntax check.

    Catches the most frequent errors instantly (no LLM call):
    1. Unterminated f-strings in print/log statements
    2. RGBColor('#hex') -> RGBColor(0xRR, 0xGG, 0xBB) for pptx and docx
    3. Spacer(N) -> Spacer(1, N) for reportlab
    4. plt.show() -> removal in headless mode
    5. Missing matplotlib.use('Agg')
    """
    fixes_applied = 0

    # 1. Fix unterminated f-strings: print(f"...{expr}) -> print(f"...{expr}")
    fixed, n = re.subn(
        r'((?:print|logger?\.\w+)\(f"[^"]*\})\)',
        r'\1")',
        code,
    )
    if n:
        code = fixed
        fixes_applied += n

    fixed, n = re.subn(
        r"""((?:print|logger?\.\w+)\(f'[^']*\})\)""",
        r"\1')",
        code,
    )
    if n:
        code = fixed
        fixes_applied += n

    # 2. RGBColor('#rrggbb') or RGBColor('rrggbb') -> RGBColor(0xrr, 0xgg, 0xbb)
    def _rgbcolor_hex_to_ints(m):
        hex_str = m.group(1) or m.group(2)
        r, g, b = hex_str[0:2], hex_str[2:4], hex_str[4:6]
        return f"RGBColor(0x{r}, 0x{g}, 0x{b})"

    fixed, n = re.subn(
        r"RGBColor\(\s*['\"]#([0-9a-fA-F]{6})['\"]\s*\)|RGBColor\(\s*['\"]([0-9a-fA-F]{6})['\"]\s*\)",
        _rgbcolor_hex_to_ints,
        code,
    )
    if n:
        code = fixed
        fixes_applied += n

    # 3. Spacer(N) -> Spacer(1, N) for reportlab
    fixed, n = re.subn(
        r"Spacer\(\s*(\d+(?:\.\d+)?)\s*\)",
        r"Spacer(1, \1)",
        code,
    )
    if n:
        code = fixed
        fixes_applied += n

    # 4. Remove plt.show() lines (headless mode)
    fixed, n = re.subn(
        r"^[ \t]*plt\.show\(\)\s*\n?",
        "",
        code,
        flags=re.MULTILINE,
    )
    if n:
        code = fixed
        fixes_applied += n

    # 5. Ensure matplotlib.use('Agg') if matplotlib.pyplot is imported
    if re.search(r"import matplotlib\.pyplot|from matplotlib", code):
        if not re.search(r"matplotlib\.use\(\s*['\"]Agg['\"]\s*\)", code):
            code = re.sub(
                r"(import matplotlib(?:\.pyplot)?[^\n]*\n)",
                r"\1import matplotlib\nmatplotlib.use('Agg')\n",
                code,
                count=1,
            )
            fixes_applied += 1

    if fixes_applied:
        log.info("[POSTPROCESS] Applied %d regex fix(es) to generated code", fixes_applied)

    return code


async def generate(analysis: dict, output_path: str) -> str:
    """Generate code using the 3-agent pipeline.

    Pipeline: Generate → Syntax Check → Fix → Execute & Review → Fix if needed → Return

    Returns the final code string. The orchestrator handles execution via sandbox.
    """
    log.info("=" * 60)
    log.info("[CODER PIPELINE] Starting 3-agent code generation pipeline")
    log.info("[CODER PIPELINE] Output path: %s", output_path)

    ci = analysis.get("coding_instructions", {})
    log.info("[CODER PIPELINE] Instructions: format=%s, title=%s, sections=%d",
             ci.get("output_format", "?"), ci.get("title", "?"), len(ci.get("sections", [])))

    user_message = json.dumps({
        "coding_instructions": ci,
        "narrative": analysis.get("narrative", {}),
        "output_path": output_path,
    }, default=str)

    pipeline_log = []
    generated_dir = str(Path(config.generated_dir).resolve())
    current_code = None
    best_code = None
    best_success = False

    for cycle in range(1, MAX_PIPELINE_CYCLES + 1):
        log.info("[CODER PIPELINE] === Cycle %d/%d ===", cycle, MAX_PIPELINE_CYCLES)
        pipeline_log.append({"cycle": cycle, "phase": "start"})

        # ── Phase 1: Generate code (or use fix from previous cycle) ──
        if current_code is None:
            log.info("[CODER PIPELINE] Phase 1: Generating code from LLM")
            current_code = await _call_llm(CODING_SYSTEM_PROMPT, user_message, label="CODEGEN")
            current_code = _postprocess_code(current_code)
            pipeline_log.append({"cycle": cycle, "phase": "code_generation", "lines": current_code.count("\n") + 1})
        else:
            log.info("[CODER PIPELINE] Phase 1: Using fix code from previous cycle")
            pipeline_log.append({"cycle": cycle, "phase": "using_fix_code"})

        # ── Phase 2: Syntax Check (catches API misuse before execution) ──
        log.info("[CODER PIPELINE] Phase 2: Running syntax checker (timeout=%ds)", AGENT_TIMEOUT_SECONDS)
        try:
            syntax_result = await asyncio.wait_for(
                syntax_checker.check(current_code),
                timeout=AGENT_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, Exception) as e:
            log.warning("[CODER PIPELINE] Syntax checker timed out or failed after %ds — skipping: %s",
                        AGENT_TIMEOUT_SECONDS, type(e).__name__)
            syntax_result = {"has_errors": False, "errors": [], "fixed_code": "", "summary": "Skipped (timeout)"}
            pipeline_log.append({"cycle": cycle, "phase": "syntax_check", "status": "skipped_timeout"})

        if syntax_result.get("has_errors", False):
            errors = syntax_result.get("errors", [])
            critical = [e for e in errors if e.get("severity") == "critical"]
            log.warning("[CODER PIPELINE] Syntax checker found %d errors (%d critical)", len(errors), len(critical))
            pipeline_log.append({"cycle": cycle, "phase": "syntax_check", "status": "errors_found",
                                 "error_count": len(errors), "critical_count": len(critical)})

            # Use fixed code from syntax checker if available
            fixed_code = syntax_result.get("fixed_code", "")
            if fixed_code and fixed_code.strip():
                log.info("[CODER PIPELINE] Using syntax checker's fixed code")
                current_code = _postprocess_code(fixed_code)
                pipeline_log.append({"cycle": cycle, "phase": "syntax_fix_applied"})
            elif critical:
                # Critical errors but no fix — ask coder LLM to regenerate
                error_feedback = "\n".join(
                    f"- Line {e.get('line', '?')}: {e.get('description', '')} → Fix: {e.get('fix', '')}"
                    for e in critical
                )
                fix_msg = (
                    "The following Python code has critical errors. Fix ALL errors and output the COMPLETE corrected script.\n\n"
                    f"ERRORS:\n{error_feedback}\n\n"
                    f"OUTPUT PATH (must save to this exact path): {output_path}\n\n"
                    f"BROKEN CODE:\n{current_code}\n\n"
                    "Output ONLY the corrected Python code. No explanation, no markdown fences."
                )
                current_code = await _call_llm(CODING_SYSTEM_PROMPT, fix_msg, label="CODEFIX-SYNTAX")
                current_code = _postprocess_code(current_code)
                pipeline_log.append({"cycle": cycle, "phase": "code_regenerated_after_syntax_errors"})
        else:
            log.info("[CODER PIPELINE] Syntax check PASSED")
            pipeline_log.append({"cycle": cycle, "phase": "syntax_check", "status": "passed"})

        # ── Phase 3: Execute & Review ──
        log.info("[CODER PIPELINE] Phase 3: Executing code and reviewing output")
        exec_result = execute_detailed(current_code, output_path)

        execution_success = exec_result["success"]
        stdout = exec_result.get("stdout", "")
        stderr = exec_result.get("stderr", "")

        if execution_success:
            log.info("[CODER PIPELINE] Execution succeeded — sending to code reviewer")
        else:
            log.warning("[CODER PIPELINE] Execution failed: %s", exec_result.get("error", "")[:200])

        # LLM-based review of execution results
        log.info("[CODER PIPELINE] Running code reviewer (timeout=%ds)", AGENT_TIMEOUT_SECONDS)
        try:
            review = await asyncio.wait_for(
                code_reviewer.review_execution(
                    code=current_code,
                    instruction=json.dumps(ci, default=str)[:2000],
                    execution_success=execution_success,
                    stdout=stdout,
                    stderr=stderr,
                    expected_output=output_path,
                    generated_dir=generated_dir,
                ),
                timeout=AGENT_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, Exception) as e:
            log.warning("[CODER PIPELINE] Code reviewer timed out or failed after %ds — skipping: %s",
                        AGENT_TIMEOUT_SECONDS, type(e).__name__)
            # If execution succeeded, treat as pass; otherwise fail
            review = {
                "verdict": "pass" if execution_success else "fail",
                "issues": [],
                "fix_code": "",
                "retry_recommended": False,
                "summary": "Skipped (timeout)",
            }
            pipeline_log.append({"cycle": cycle, "phase": "code_review", "status": "skipped_timeout"})

        verdict = review.get("verdict", "fail")
        pipeline_log.append({"cycle": cycle, "phase": "code_review", "verdict": verdict,
                             "summary": review.get("summary", "")[:200]})

        # Track best code
        if execution_success:
            best_code = current_code
            best_success = True

        # ── Decision: Pass or Fix ──
        if verdict == "pass":
            log.info("[CODER PIPELINE] === PASSED on cycle %d ===", cycle)
            pipeline_log.append({"cycle": cycle, "phase": "complete", "status": "passed"})
            log.info("[CODER PIPELINE] Pipeline log: %s", json.dumps(pipeline_log, default=str)[:500])
            return current_code

        if verdict == "partial" and execution_success:
            log.info("[CODER PIPELINE] Partial success on cycle %d with files generated", cycle)
            if cycle >= MAX_PIPELINE_CYCLES:
                pipeline_log.append({"cycle": cycle, "phase": "complete", "status": "partial_accepted"})
                return current_code

        # Failed — try fix code from reviewer
        fix_code = review.get("fix_code", "")
        retry_recommended = review.get("retry_recommended", False)

        if fix_code and fix_code.strip() and retry_recommended and cycle < MAX_PIPELINE_CYCLES:
            log.info("[CODER PIPELINE] Using reviewer's fix code for next cycle")
            current_code = _postprocess_code(fix_code)
            pipeline_log.append({"cycle": cycle, "phase": "fix_code_received"})
            continue

        # No fix from reviewer — try our own fix via LLM
        if cycle < MAX_PIPELINE_CYCLES:
            error_msg = exec_result.get("error", "") or review.get("summary", "Unknown error")
            log.info("[CODER PIPELINE] Generating fix via coder LLM")
            fix_msg = (
                "The following Python code has an error. Fix it and output the COMPLETE corrected Python script.\n\n"
                f"ERROR:\n{error_msg}\n\n"
                f"OUTPUT PATH (must save to this exact path): {output_path}\n\n"
                f"BROKEN CODE:\n{current_code}\n\n"
                "Output ONLY the corrected Python code. No explanation, no markdown fences."
            )
            current_code = await _call_llm(CODING_SYSTEM_PROMPT, fix_msg, label="CODEFIX")
            current_code = _postprocess_code(current_code)
            pipeline_log.append({"cycle": cycle, "phase": "coder_fix_generated"})
            continue

    # All cycles exhausted — return best code or last code
    log.warning("[CODER PIPELINE] All %d cycles exhausted", MAX_PIPELINE_CYCLES)
    if best_code and best_success:
        log.info("[CODER PIPELINE] Returning best successful code from earlier cycle")
        return best_code

    log.info("[CODER PIPELINE] Returning last code attempt")
    return current_code


async def fix_code(code: str, error: str, output_path: str) -> str:
    """Legacy fix_code interface — still used by orchestrator retry loop."""
    log.info("=" * 60)
    log.info("[CODEFIX] Attempting to fix code generation error")
    log.info("[CODEFIX] Error: %s", error[:200])

    user_message = (
        "The following Python code has an error. Fix it and output the COMPLETE corrected Python script.\n\n"
        f"ERROR:\n{error}\n\n"
        f"OUTPUT PATH (must save to this exact path): {output_path}\n\n"
        f"BROKEN CODE:\n{code}\n\n"
        "Output ONLY the corrected Python code. No explanation, no markdown fences."
    )

    fixed = await _call_llm(CODING_SYSTEM_PROMPT, user_message, label="CODEFIX")
    fixed = _postprocess_code(fixed)
    log.info("[CODEFIX] Fixed code length: %d chars, ~%d lines", len(fixed), fixed.count("\n") + 1)
    return fixed
