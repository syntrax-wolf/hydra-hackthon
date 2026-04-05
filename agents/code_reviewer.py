"""Code Reviewer Agent — executes code, verifies output, provides critique.

Receives generated code, executes it in the sandbox, inspects results
(files generated, stdout, errors), and provides a detailed critique.
If issues are found, returns fix instructions and corrected code.
"""

import json
import os
import re
import logging
import time
import httpx
from pathlib import Path
from core.config import config

log = logging.getLogger("code_reviewer")

MAX_RETRIES = 2  # Max retry attempts for LLM calls (total attempts = MAX_RETRIES + 1)

CODE_REVIEWER_SYSTEM_PROMPT = """You are a meticulous code execution reviewer, quality assurance specialist, and visual design critic. You evaluate code on THREE dimensions: execution correctness, output completeness, and visual design quality.

═══════════════════════════════════════════════
DIMENSION 1: EXECUTION CORRECTNESS
═══════════════════════════════════════════════
- Did the code execute successfully without exceptions?
- Were all expected output files actually created?
- Are there any warnings in stderr that indicate partial failure?
- Did the process complete within timeout?

═══════════════════════════════════════════════
DIMENSION 2: OUTPUT COMPLETENESS
═══════════════════════════════════════════════

For CHARTS (.png):
  - Were chart files created with reasonable size (> 5KB)?
  - Is plt.close() called after each plt.savefig()?
  - Are charts using real data (not empty/placeholder)?

For PDF:
  - Was the .pdf created and non-empty?
  - Does code call doc.build(elements)?
  - Are chart images embedded via Image() flowable?
  - Is there a title/cover page?
  - Are there at least 2-3 embedded chart images?

For PPTX:
  - Was the .pptx created and non-empty?
  - Were slides added properly (prs.slides.add_slide)?
  - Are chart images embedded on slides (add_picture)?
  - Is there a title slide?
  - Are there at least 4-6 slides for a proper presentation?

For XLSX:
  - Was the .xlsx created and non-empty?
  - Is header formatting applied?

═══════════════════════════════════════════════
DIMENSION 3: VISUAL DESIGN QUALITY
═══════════════════════════════════════════════

This is CRITICAL. A document that runs but looks ugly is a PARTIAL success, not a pass.
Check the code for these visual design elements:

A) COLOR USAGE (check the code, not the output):
  GOOD signs:
  - Color constants defined at top (COLOR_PRIMARY, etc.)
  - Semantic colors (green for positive, red for negative)
  - CHART_COLORS list with 4+ coordinated colors
  - Table header with dark background and white text
  - Alternating row colors in tables
  BAD signs:
  - No color definitions at all (using defaults everywhere)
  - Only 1-2 colors used throughout
  - Pure black/white everywhere
  - Random or clashing colors

B) CHART QUALITY (check the code):
  GOOD signs:
  - ax.spines['top'].set_visible(False) — clean axes
  - ax.grid(axis='y', ...) — subtle gridlines
  - ax.bar_label() or annotations — value labels
  - Proper title with fontweight='bold'
  - facecolor set to light background
  - plt.tight_layout() called
  BAD signs:
  - Default matplotlib style (no customization)
  - Missing titles or axis labels
  - No value labels on bars
  - Cluttered or overlapping text

C) TABLE STYLING (check the code):
  GOOD signs:
  - TableStyle commands for header background color
  - LINEBELOW, LINEABOVE for borders
  - ROWBACKGROUNDS for alternating stripes
  - FONTNAME/FONTSIZE specified
  - Number alignment (right-align)
  BAD signs:
  - No TableStyle at all (plain unstyled table)
  - No header distinction
  - No cell padding (TOPPADDING, BOTTOMPADDING)

D) VISUAL ELEMENTS GENERATED:
  GOOD signs:
  - PIL-generated metric cards / KPI strip
  - Section banners or dividers
  - Title page with background color/gradient
  - Page numbers in PDF
  BAD signs:
  - Text-only document with no visual elements
  - No metric cards when KPI data is available
  - No title/cover page

E) TYPOGRAPHY & SPACING:
  GOOD signs:
  - Multiple font sizes creating hierarchy
  - Bold on headings, regular on body
  - Spacer elements between sections (PDF)
  - Proper margins from edges (PPTX)
  BAD signs:
  - Same font size everywhere
  - No spacing between sections
  - Content touching slide/page edges

═══════════════════════════════════════════════
ERROR PATTERN RECOGNITION
═══════════════════════════════════════════════
Pattern: "RGBColor() takes exactly 3 integer arguments"
Fix: Replace RGBColor('#hex') with RGBColor(0xRR, 0xGG, 0xBB)

Pattern: "Spacer() takes at least 2 arguments"
Fix: Replace Spacer(12) with Spacer(1, 12)

Pattern: "'Presentation' object has no attribute 'add_slide'"
Fix: Use prs.slides.add_slide(layout) not prs.add_slide(layout)

Pattern: "module 'matplotlib' has no attribute 'pyplot'"
Fix: import matplotlib.pyplot as plt (not just matplotlib)

Pattern: "No such file or directory: 'chart_1.png'"
Fix: Charts must be generated BEFORE they are embedded in the document

Pattern: "cannot identify image file"
Fix: Ensure PIL images are saved as RGB (not RGBA) before embedding

Pattern: Font-related errors (truetype not found)
Fix: Always use try/except with ImageFont.load_default() fallback

═══════════════════════════════════════════════
VERDICT DECISION RULES
═══════════════════════════════════════════════

PASS = execution succeeded AND files created AND visual design is reasonable
  (has colors, charts embedded, table styling, some visual elements)

PARTIAL = execution succeeded AND files created BUT visual design is poor
  (no colors, no chart styling, plain tables, no visual elements)
  → In this case, provide fix_code that adds visual improvements

FAIL = execution failed OR expected files not created
  → MUST provide fix_code with the complete corrected script

═══════════════════════════════════════════════
RESPONSE FORMAT
═══════════════════════════════════════════════
{
  "verdict": "pass" | "fail" | "partial",
  "issues": [
    {
      "type": "runtime_error|missing_file|empty_output|design_quality|warning",
      "description": "What went wrong or what is missing",
      "root_cause": "Why it happened",
      "fix_instruction": "Specific code change to fix or improve this"
    }
  ],
  "files_verified": ["list of files confirmed created"],
  "quality_notes": "Assessment of visual design quality",
  "fix_code": "If verdict is fail or partial, provide COMPLETE corrected Python code. If pass, empty string.",
  "retry_recommended": true/false,
  "summary": "One-line summary"
}

CRITICAL RULES:
1. The fix_code must be COMPLETE and SELF-CONTAINED — not a patch, the full corrected script.
2. Set retry_recommended=true only if you believe the fix will succeed.
3. When providing fix_code for visual improvements (partial verdict), add:
   - Color constants at the top
   - Styled charts with proper formatting
   - Table styling with alternating rows
   - PIL-generated metric cards if KPI data exists
   - Section banners for major sections
   - Proper title page with background
4. A document with NO charts despite having data is always "partial" at best.
5. A document that is text-only with no visual styling is always "partial" at best."""


async def _call_llm(system_prompt: str, user_message: str, label: str = "REVIEWER") -> str:
    """Call OpenRouter for code review."""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            log.info("[%s] Calling OpenRouter (attempt=%d/%d)...", label, attempt + 1, MAX_RETRIES + 1)
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
                        "max_tokens": 8192,
                        "temperature": 0.1,
                        "top_p": 0.95,
                        "reasoning": {"effort": "high"},
                    },
                )
                elapsed = time.time() - t0
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # Strip <think> blocks
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                # Strip markdown fences
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()
                if content and not content.startswith("{"):
                    idx = content.find("{")
                    if idx > 0:
                        content = content[idx:]

                log.info("[%s] Response in %.1fs", label, elapsed)
                return content.strip()
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_error = e
            log.warning("[%s] Attempt %d failed: %s", label, attempt + 1, e)
            if attempt < MAX_RETRIES:
                import asyncio
                await asyncio.sleep(2)
    raise last_error


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {
        "verdict": "fail",
        "issues": [],
        "files_verified": [],
        "quality_notes": "LLM response unparseable",
        "fix_code": "",
        "retry_recommended": False,
        "summary": "Could not parse reviewer response",
    }


def verify_files(generated_dir: str, expected_output: str) -> dict:
    """Verify that expected output file exists and check for other generated files."""
    verified = []
    missing = []
    empty = []

    generated_path = Path(generated_dir)
    expected_path = Path(expected_output)

    # Check the primary expected output
    if expected_path.exists():
        size = expected_path.stat().st_size
        if size > 0:
            verified.append({"path": str(expected_path), "size": size})
        else:
            empty.append(str(expected_path))
    else:
        missing.append(str(expected_path))

    # Also find any chart PNGs that were generated
    if generated_path.is_dir():
        for f in generated_path.iterdir():
            if f.suffix.lower() == ".png" and f != expected_path:
                size = f.stat().st_size
                if size > 0:
                    verified.append({"path": str(f), "size": size})

    return {
        "verified": verified,
        "missing": missing,
        "empty": empty,
        "all_ok": len(missing) == 0 and len(empty) == 0,
    }


async def review_execution(
    code: str,
    instruction: str,
    execution_success: bool,
    stdout: str,
    stderr: str,
    expected_output: str,
    generated_dir: str,
) -> dict:
    """Review code execution results and provide critique.

    Args:
        code: The executed Python code
        instruction: Original coding instruction (for context)
        execution_success: Whether subprocess exited 0
        stdout: Captured stdout
        stderr: Captured stderr
        expected_output: Path to expected output file
        generated_dir: Directory where files are generated

    Returns:
        dict with: verdict, issues, fix_code, retry_recommended, files_verified
    """
    # Verify files
    files_status = verify_files(generated_dir, expected_output)

    # Build context for LLM review
    context_parts = [
        f"## Original Instruction\n{instruction[:500]}",
        f"\n## Execution Result",
        f"Success: {execution_success}",
        f"Stdout (last 1500 chars): {stdout[-1500:] if stdout else 'None'}",
        f"Stderr (last 500 chars): {stderr[-500:] if stderr else 'None'}",
        f"\n## File Verification",
        "Verified files: " + str([(item["path"], item["size"]) for item in files_status["verified"]]),
        f"Missing files: {files_status['missing']}",
        f"Empty files: {files_status['empty']}",
        f"\n## Code (for analysis)\n```python\n{code[:8000]}\n```",
    ]

    raw = await _call_llm(
        CODE_REVIEWER_SYSTEM_PROMPT,
        "\n".join(context_parts),
        label="REVIEWER",
    )
    result = _parse_json(raw)

    # Ensure defaults
    if not execution_success and result.get("verdict") == "pass":
        result["verdict"] = "fail"
    result.setdefault("verdict", "pass" if execution_success and files_status["all_ok"] else "fail")
    result.setdefault("issues", [])
    result.setdefault("files_verified", [f["path"] for f in files_status["verified"]])
    result.setdefault("fix_code", "")
    result.setdefault("retry_recommended", not execution_success)
    result.setdefault("summary", "Review complete")

    log.info("[REVIEWER] Verdict: %s — %s", result["verdict"], result["summary"])
    if result["issues"]:
        for issue in result["issues"][:5]:
            log.info("[REVIEWER]   Issue: [%s] %s", issue.get("type", "?"), issue.get("description", "")[:100])

    return result
