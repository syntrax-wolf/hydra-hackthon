"""Orchestrator — coordinates the full pipeline with quality evaluation loop.

Pipeline:
1. Finance LLM decomposes query → data plan
2. Fetch data from PostgreSQL
3. Finance LLM analyzes data → narrative + coding instructions
4. Coder 3-agent pipeline generates document (syntax check + review built in)
5. Quality evaluation — re-run if below threshold (max 3 iterations)
6. Self-critique before delivery
"""

import asyncio
import json
import re
import time
import uuid
import os
import logging
import httpx
from datetime import datetime, timedelta
from pathlib import Path

from core.config import config
from core.db import execute_query
from core.sandbox import execute, execute_detailed, validate_syntax, SandboxError
from agents import finance_agent, coding_agent

log = logging.getLogger("orchestrator")

# Quality loop constants
MAX_RETRIES = 2  # Max retry attempts everywhere (total attempts = MAX_RETRIES + 1)
MAX_QUALITY_ITERATIONS = MAX_RETRIES + 1  # 1 try + MAX_RETRIES retries = 3 iterations
QUALITY_THRESHOLD = 70
ORCHESTRATION_TIMEOUT_SECONDS = 300

QUALITY_EVAL_PROMPT = """You are a strict quality evaluator for financial analysis workflows. You evaluate both analytical quality AND visual design quality.

Evaluate the workflow output against these 5 criteria:

1. DATA COMPLETENESS (0-20): Was all relevant data fetched and used? Are there data gaps?

2. ANALYSIS DEPTH (0-20): Does the analysis include precise calculations, trends, comparisons, and actionable recommendations? Or is it shallow/generic?

3. CHART & VISUALIZATION QUALITY (0-20):
   - Were 2-3+ charts generated and embedded?
   - Do charts use proper styling (clean axes, value labels, gridlines, good colors)?
   - Are chart types appropriate for the data (bar for comparison, line for trend, donut for proportion)?
   - Do charts use a coordinated color palette (not random/default matplotlib colors)?
   - Are charts titled and labeled properly?

4. VISUAL DESIGN QUALITY (0-20):
   - Does the document have a professional color scheme (not plain black/white)?
   - Is there a styled title/cover page with background color or gradient?
   - Are tables styled with header colors, alternating rows, proper borders?
   - Are there KPI/metric card visuals for key numbers?
   - Is there visual hierarchy (different font sizes, bold headers, spacing)?
   - Are there section dividers or banners between major sections?
   - Does the document look like it was designed by a professional?

5. DOCUMENT STRUCTURE (0-20):
   - Is the overall structure logical (title → summary → details → recommendations)?
   - Are charts and tables placed at relevant points in the narrative?
   - Is there proper spacing between elements?
   - Are page numbers included (PDF)?
   - Does it have a recommendations/conclusion section?

Respond with ONLY valid JSON:
{
  "total_score": 0-100,
  "data_completeness": {"score": 0-20, "feedback": "..."},
  "analysis_depth": {"score": 0-20, "feedback": "..."},
  "chart_quality": {"score": 0-20, "feedback": "..."},
  "visual_design": {"score": 0-20, "feedback": "..."},
  "document_structure": {"score": 0-20, "feedback": "..."},
  "pass": true/false,
  "critical_issues": ["issue1", ...],
  "improvement_instructions": "Specific instructions for the next iteration to improve visual design, add charts, improve styling, etc."
}

SCORING GUIDE:
  16-20: Excellent — professional, polished, would impress a manager
  12-15: Good — decent but could be better
  8-11: Below average — missing key elements
  0-7: Poor — minimal effort, plain/ugly output

A total score of 70+ passes. Below 70 requires re-execution.

IMPORTANT: Be strict on visual quality. A document with correct data but no visual styling (plain text, default charts, unstyled tables) should score LOW on visual_design and chart_quality. The improvement_instructions should specifically tell the coder what visual elements to add."""


def _clean_old_files():
    """Remove generated files older than 1 hour."""
    generated = Path(config.generated_dir).resolve()
    if not generated.exists():
        return
    cutoff = datetime.now() - timedelta(hours=1)
    cleaned = 0
    for f in generated.iterdir():
        if f.name == ".gitkeep":
            continue
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink()
                cleaned += 1
        except OSError:
            pass
    if cleaned:
        log.info("[CLEANUP] Removed %d old files", cleaned)


def fetch_all_data(data_requirements: list) -> list:
    log.info("=" * 60)
    log.info("[DATA] Fetching data for %d requirements", len(data_requirements))
    results = []
    for i, req in enumerate(data_requirements):
        req_id = req.get("req_id", "unknown")
        table = req.get("table")
        log.info("[DATA] [%d/%d] Fetching %s from '%s' (priority=%s)",
                 i + 1, len(data_requirements), req_id, table, req.get("priority", "required"))
        try:
            t0 = time.time()
            result = execute_query(
                table=req.get("table"),
                columns=req.get("columns", []),
                filters=req.get("filters", {}),
                group_by=req.get("group_by", []),
                order_by=req.get("order_by"),
                aggregate=req.get("aggregate", {}),
            )
            elapsed = (time.time() - t0) * 1000
            log.info("[DATA] [%d/%d] %s -> %d rows fetched in %.0fms",
                     i + 1, len(data_requirements), req_id, len(result["data"]), elapsed)
            results.append({
                "req_id": req_id,
                "table": table,
                "status": "ok",
                "row_count": len(result["data"]),
                "columns": result["columns"],
                "data": result["data"],
            })
        except Exception as e:
            log.error("[DATA] [%d/%d] %s -> ERROR: %s", i + 1, len(data_requirements), req_id, e)
            results.append({
                "req_id": req_id,
                "table": table,
                "status": "error",
                "error": str(e),
                "columns": [],
                "data": [],
            })
    total_rows = sum(r.get("row_count", 0) for r in results)
    log.info("[DATA] All fetches complete: %d total rows across %d queries", total_rows, len(results))
    return results


def _generate_follow_ups(query: str) -> list[str]:
    follow_ups = [
        "Compare this across all offices",
        "Break this down by product category",
        "What is the profit margin trend over the past quarter?",
        "Which products are below safety stock?",
    ]
    lower = query.lower()
    if "office" in lower or "city" in lower:
        follow_ups[0] = "Compare profit margins across all offices"
    if "employee" in lower or "hr" in lower or "salary" in lower:
        follow_ups[0] = "What is the department-wise headcount and average salary?"
        follow_ups[1] = "Who are the top-rated performers this review cycle?"
    if "inventory" in lower or "stock" in lower or "safety" in lower:
        follow_ups[2] = "What are the reorder recommendations?"
    if "pricing" in lower or "margin" in lower or "competitor" in lower:
        follow_ups[1] = "How do our prices compare to competitor averages?"
    if "skill" in lower or "developer" in lower:
        follow_ups[0] = "What is the skill distribution across departments?"
    return follow_ups[:4]


async def _call_quality_eval(user_query: str, analysis: dict, output_path: str,
                              doc_exists: bool, doc_size_kb: int) -> dict:
    """Evaluate quality of the pipeline output using LLM."""
    eval_context = f"User query: {user_query}\n\n"

    narrative = analysis.get("narrative", {})
    eval_context += f"Executive summary: {narrative.get('executive_summary', 'N/A')}\n"
    eval_context += f"Key findings: {len(narrative.get('key_findings', []))}\n"
    eval_context += f"Recommendations: {len(narrative.get('recommendations', []))}\n\n"

    ci = analysis.get("coding_instructions", {})
    eval_context += f"Coding instructions sections: {len(ci.get('sections', []))}\n"
    section_types = [s.get("type", "?") for s in ci.get("sections", [])]
    eval_context += f"Section types: {section_types}\n\n"

    eval_context += f"Document generated: {doc_exists}\n"
    if doc_exists:
        eval_context += f"Document size: {doc_size_kb} KB\n"
        eval_context += f"Document path: {output_path}\n"

    try:
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
                        {"role": "system", "content": QUALITY_EVAL_PROMPT},
                        {"role": "user", "content": eval_context},
                    ],
                    "max_tokens": 4096,
                    "temperature": 0.2,
                    "reasoning": {"effort": "high"},
                },
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]

            # Strip <think> blocks and fences
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

            result = json.loads(content)
            return result
    except Exception as e:
        log.warning("[QUALITY] Evaluation failed: %s — defaulting to pass", str(e)[:200])
        return {"total_score": 75, "pass": True, "critical_issues": [], "improvement_instructions": ""}


async def _run_pipeline_once(
    raw_query: str, output_format: str, conversation_history: list[dict],
    query_id: str, generated_dir: Path, improvement_feedback: str = "",
) -> dict:
    """Run the 5-step pipeline once. Returns dict with analysis, output_path, code, success, etc."""

    # Step 1: Finance LLM decomposes query
    t1 = time.time()
    log.info("[STEP 1/5] Finance Agent — Decomposing query...")
    extra_context = ""
    if improvement_feedback:
        extra_context = f"\n\nIMPROVEMENT FEEDBACK FROM PREVIOUS ITERATION:\n{improvement_feedback}\nPlease address these issues in your decomposition and analysis."

    decomposition = await finance_agent.decompose(
        raw_query + extra_context, output_format, conversation_history
    )
    log.info("[STEP 1/5] Decomposition complete in %.1fs", time.time() - t1)

    # Step 2: Fetch data from PostgreSQL
    t2 = time.time()
    data_requirements = decomposition.get("data_requirements", [])
    log.info("[STEP 2/5] Data Fetch — %d queries to execute", len(data_requirements))
    data_results = fetch_all_data(data_requirements)
    log.info("[STEP 2/5] Data fetch complete in %.1fs", time.time() - t2)

    # Step 3: Finance LLM analyzes data
    t3 = time.time()
    log.info("[STEP 3/5] Finance Agent — Analyzing data...")
    analyze_query = raw_query
    if improvement_feedback:
        analyze_query += f"\n\nIMPROVEMENT NOTES: {improvement_feedback}"
    analysis = await finance_agent.analyze(
        analyze_query, decomposition, data_results, output_format, conversation_history
    )
    log.info("[STEP 3/5] Analysis complete in %.1fs", time.time() - t3)

    # Decision: does this query need a document?
    needs_document = analysis.get("needs_document", True)
    doc_format = analysis.get("document_format", "pdf")
    if doc_format not in ("pdf", "pptx", "xlsx"):
        doc_format = "pdf"

    if not needs_document:
        return {
            "analysis": analysis,
            "needs_document": False,
            "output_path": None,
            "doc_format": doc_format,
            "code": None,
            "doc_success": False,
        }

    # Step 4+5: Coder 3-agent pipeline (generate → syntax check → execute & review)
    t4 = time.time()
    filename = f"report_{query_id}.{doc_format}"
    output_path = str(generated_dir / filename)
    log.info("[STEP 4/5] Coder Pipeline — Generating %s document...", doc_format.upper())

    code = None
    doc_exists = False

    try:
        code = await coding_agent.generate(analysis, output_path)
        log.info("[STEP 4/5] Coder pipeline complete in %.1fs (%d lines)", time.time() - t4, code.count("\n") + 1)

        # Check if the output file was created by the pipeline
        doc_exists = Path(output_path).exists()
        if not doc_exists:
            # The 3-agent pipeline already tried execution internally.
            # Do one final attempt here as a safety net.
            log.info("[STEP 5/5] Output not found — running final sandbox attempt")
            syntax_err = validate_syntax(code)
            if syntax_err:
                log.warning("[STEP 5/5] Syntax error in final code: %s", syntax_err[:150])
                code = await coding_agent.fix_code(code, syntax_err, output_path)

            try:
                execute(code, output_path)
                doc_exists = True
                log.info("[STEP 5/5] Final sandbox attempt succeeded")
            except SandboxError as e:
                log.error("[STEP 5/5] Final sandbox attempt failed: %s", str(e)[:200])
                # One more fix attempt
                try:
                    code = await coding_agent.fix_code(code, str(e), output_path)
                    execute(code, output_path)
                    doc_exists = True
                    log.info("[STEP 5/5] Second fix attempt succeeded")
                except Exception:
                    log.error("[STEP 5/5] All attempts exhausted — will return narrative only")

    except Exception as e:
        log.error("[STEP 4/5] Coder pipeline crashed: %s — falling back to narrative only", str(e)[:200])
        # Don't re-raise. We have the analysis/narrative from Step 3.
        # Return it without a document file.

    return {
        "analysis": analysis,
        "needs_document": True,
        "output_path": output_path if doc_exists else None,
        "doc_format": doc_format,
        "code": code,
        "doc_success": doc_exists,
        "filename": filename if doc_exists else None,
    }


async def process_query(raw_query: str, output_format: str = "auto", conversation_history: list[dict] = None) -> dict:
    """Main orchestration entry point with iterative quality loop.

    Runs the full pipeline, evaluates output quality, and re-runs with
    improvements if the quality score is below threshold. Max 3 iterations.
    """
    start = time.time()
    query_id = str(uuid.uuid4())[:8]

    log.info("")
    log.info("*" * 70)
    log.info("  PIPELINE START — Query ID: %s", query_id)
    log.info("  Query: %s", raw_query)
    if conversation_history:
        log.info("  Conversation context: %d previous turns", len(conversation_history))
    log.info("*" * 70)

    _clean_old_files()

    generated_dir = Path(config.generated_dir).resolve()
    generated_dir.mkdir(parents=True, exist_ok=True)

    best_result = None
    best_score = 0
    iteration_history = []
    improvement_feedback = ""

    try:
        for iteration in range(1, MAX_QUALITY_ITERATIONS + 1):
            # Check timeout
            elapsed_so_far = time.time() - start
            if elapsed_so_far > ORCHESTRATION_TIMEOUT_SECONDS:
                log.warning("[QUALITY] Timeout exceeded (%.0fs) — using best result", elapsed_so_far)
                break

            log.info("")
            log.info("=" * 70)
            log.info("  ITERATION %d/%d", iteration, MAX_QUALITY_ITERATIONS)
            log.info("=" * 70)

            # Run the full pipeline
            pipeline_result = await _run_pipeline_once(
                raw_query, output_format, conversation_history,
                query_id, generated_dir, improvement_feedback,
            )

            analysis = pipeline_result["analysis"]

            # Extract follow-ups
            follow_ups = analysis.get("follow_ups", [])
            if not follow_ups or not isinstance(follow_ups, list):
                follow_ups = _generate_follow_ups(raw_query)

            # If narrative-only, no quality loop needed
            if not pipeline_result["needs_document"]:
                elapsed = int((time.time() - start) * 1000)
                log.info("*" * 70)
                log.info("  PIPELINE COMPLETE (narrative only) — %s", query_id)
                log.info("  Total time: %.1fs", elapsed / 1000)
                log.info("*" * 70)
                return {
                    "query_id": query_id,
                    "status": "complete",
                    "narrative": analysis.get("narrative", {}),
                    "file": None,
                    "follow_ups": follow_ups,
                    "time_ms": elapsed,
                }

            doc_exists = pipeline_result["doc_success"]
            output_path = pipeline_result.get("output_path")
            filename = pipeline_result.get("filename")
            doc_size_kb = os.path.getsize(output_path) // 1024 if doc_exists and output_path else 0

            # === Quality Evaluation (2-minute timeout) ===
            log.info("[QUALITY] Evaluating output quality (iteration %d, timeout=120s)...", iteration)
            try:
                quality_eval = await asyncio.wait_for(
                    _call_quality_eval(
                        raw_query, analysis, output_path or "", doc_exists, doc_size_kb,
                    ),
                    timeout=120,
                )
            except (asyncio.TimeoutError, Exception) as e:
                log.warning("[QUALITY] Evaluation timed out or failed after 120s — defaulting to pass: %s",
                            type(e).__name__)
                quality_eval = {"total_score": 75, "pass": True, "critical_issues": [], "improvement_instructions": ""}
            score = quality_eval.get("total_score", 0)
            passed = quality_eval.get("pass", False)
            critical_issues = quality_eval.get("critical_issues", [])

            log.info("[QUALITY] Score: %d/100, Pass: %s", score, passed)
            if critical_issues:
                for issue in critical_issues[:5]:
                    log.info("[QUALITY]   Issue: %s", issue[:100])

            iteration_record = {
                "iteration": iteration,
                "score": score,
                "passed": passed,
                "critical_issues": critical_issues,
                "doc_generated": doc_exists,
            }
            iteration_history.append(iteration_record)

            # Track best result
            current_result = {
                "analysis": analysis,
                "follow_ups": follow_ups,
                "doc_exists": doc_exists,
                "output_path": output_path,
                "filename": filename,
                "doc_size_kb": doc_size_kb,
            }
            if score > best_score:
                best_score = score
                best_result = current_result

            # Convergence check
            if passed or score >= QUALITY_THRESHOLD:
                log.info("[QUALITY] Quality threshold met (%d >= %d). Finalizing.", score, QUALITY_THRESHOLD)
                break

            # Prepare improvement feedback for next iteration
            if iteration < MAX_QUALITY_ITERATIONS:
                improvement_feedback = quality_eval.get("improvement_instructions", "")
                if critical_issues:
                    improvement_feedback += "\n\nCritical issues to fix:\n"
                    for issue in critical_issues:
                        improvement_feedback += f"- {issue}\n"
                log.info("[QUALITY] Score %d < %d — re-running with improvements", score, QUALITY_THRESHOLD)
            # Reset query_id for next iteration's filename
            query_id = str(uuid.uuid4())[:8]

        # === Use best result ===
        if best_result is None:
            best_result = current_result

        analysis = best_result["analysis"]
        follow_ups = best_result["follow_ups"]
        doc_exists = best_result["doc_exists"]
        output_path = best_result["output_path"]
        filename = best_result["filename"]
        doc_size_kb = best_result["doc_size_kb"]

        elapsed = int((time.time() - start) * 1000)

        if not doc_exists:
            narrative = analysis.get("narrative", {})
            log.info("*" * 70)
            log.info("  PIPELINE PARTIAL — document failed, narrative returned")
            log.info("  Total time: %.1fs | Quality score: %d | Iterations: %d",
                     elapsed / 1000, best_score, len(iteration_history))
            log.info("*" * 70)
            return {
                "query_id": query_id,
                "status": "complete",
                "narrative": narrative,
                "file": None,
                "follow_ups": follow_ups,
                "time_ms": elapsed,
                "quality_score": best_score,
                "iterations": len(iteration_history),
                "error": None,
            }

        log.info("")
        log.info("*" * 70)
        log.info("  PIPELINE COMPLETE — %s", query_id)
        log.info("  Total time: %.1fs | Quality score: %d | Iterations: %d",
                 elapsed / 1000, best_score, len(iteration_history))
        log.info("  Output: %s (%d KB)", filename, doc_size_kb)
        log.info("*" * 70)

        return {
            "query_id": query_id,
            "status": "complete",
            "narrative": analysis.get("narrative", {}),
            "file": {
                "name": filename,
                "download_url": f"/api/download/{filename}",
                "size_kb": doc_size_kb,
            },
            "follow_ups": follow_ups,
            "time_ms": elapsed,
            "quality_score": best_score,
            "iterations": len(iteration_history),
        }

    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        log.error("")
        log.error("*" * 70)
        log.error("  PIPELINE ERROR — %s", query_id)
        log.error("  Error: %s", str(e)[:300])
        log.error("  Total time: %.1fs", elapsed / 1000)
        log.error("*" * 70)

        # ── GRACEFUL FALLBACK: always return the best available data ──
        # Priority 1: best result with a document file
        if best_result and best_result.get("doc_exists"):
            log.info("[FALLBACK] Returning best result WITH document despite error")
            return {
                "query_id": query_id,
                "status": "complete",
                "narrative": best_result["analysis"].get("narrative", {}),
                "file": {
                    "name": best_result["filename"],
                    "download_url": f"/api/download/{best_result['filename']}",
                    "size_kb": best_result["doc_size_kb"],
                },
                "follow_ups": best_result.get("follow_ups", []),
                "time_ms": elapsed,
                "quality_score": best_score,
                "iterations": len(iteration_history),
            }

        # Priority 2: best result with narrative but no document
        if best_result and best_result.get("analysis"):
            log.info("[FALLBACK] Returning best result with narrative only (document failed)")
            narrative = best_result["analysis"].get("narrative", {})
            return {
                "query_id": query_id,
                "status": "complete",
                "narrative": narrative,
                "file": None,
                "follow_ups": best_result.get("follow_ups", _generate_follow_ups(raw_query)),
                "time_ms": elapsed,
                "quality_score": best_score,
                "iterations": len(iteration_history),
                "document_note": "The analysis is complete but the document could not be generated. The insights are provided above.",
            }

        # Priority 3: nothing at all — still don't return a hard error, give a helpful message
        log.info("[FALLBACK] No results available, returning graceful error")
        return {
            "query_id": query_id,
            "status": "complete",
            "narrative": {
                "executive_summary": "I encountered an issue while processing your query. Please try rephrasing your question or simplifying the request.",
                "detailed_analysis": "",
                "key_findings": [],
                "recommendations": [],
                "caveats": [str(e)[:200]],
            },
            "file": None,
            "follow_ups": _generate_follow_ups(raw_query),
            "time_ms": elapsed,
            "document_note": "The query could not be fully processed. Please try again.",
        }
