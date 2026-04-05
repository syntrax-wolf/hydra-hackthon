"""HydraDB Retriever — Recall helper for the applicant flow.

Wraps HydraDB's full_recall API and provides helper functions
to convert results into structured job data or LLM-ready context.

Uses the hydra_agent tenant with sub-tenants:
  - job_postings: all job posting data
  - applicant_profiles: applicant profile memories
  - applications: application records
  - schema: DB schema documentation
"""

import os
import json
import logging
from typing import Any

log = logging.getLogger("hydra_retriever")

HYDRA_API_KEY = os.environ.get("HYDRA_API_KEY", "")
TENANT_ID = os.environ.get("HYDRA_TENANT_ID", "hydra_agent")

_client = None


def _get_client():
    global _client
    if _client is None:
        from hydra_db import HydraDB
        _client = HydraDB(token=HYDRA_API_KEY)
    return _client


# ── Context builder (from HydraDB docs) ─────────────────────────

def _format_path_chain(path: Any) -> str:
    if not isinstance(path, dict):
        return str(path)
    triplets = path.get("triplets") or []
    parts = []
    for t in triplets:
        src = (t.get("source") or {}).get("name", "")
        tgt = (t.get("target") or {}).get("name", "")
        rel = t.get("relation") or {}
        pred = rel.get("canonical_predicate", "")
        line = f"[{src}] -> {pred} -> [{tgt}]"
        ctx = rel.get("context")
        if ctx:
            line += f": {ctx}"
        temporal = rel.get("temporal_details")
        if temporal:
            line += f" [Time: {temporal}]"
        parts.append(line)
    return "\n  ↳ ".join(parts)


def build_context_string(result: dict[str, Any]) -> str:
    """Build formatted context string from HydraDB full_recall response."""
    lines: list[str] = []

    # Handle SDK response object — convert to dict if needed
    if hasattr(result, "__dict__"):
        result = _response_to_dict(result)

    gc: dict[str, Any] = result.get("graph_context") or {}

    # Entity paths
    query_paths = gc.get("query_paths") or []
    if query_paths:
        lines.append("=== ENTITY PATHS ===")
        for path in query_paths:
            lines.append(_format_path_chain(path))
        lines.append("")

    # Chunks
    chunks: list[dict[str, Any]] = result.get("chunks") or []
    additional_context: dict[str, Any] = result.get("additional_context") or {}
    chunk_id_to_group_ids: dict[str, list[str]] = gc.get("chunk_id_to_group_ids") or {}
    chunk_relations: list[dict[str, Any]] = gc.get("chunk_relations") or []

    if chunks:
        lines.append("=== CONTEXT ===")
        for i, chunk in enumerate(chunks):
            lines.append(f"Chunk {i + 1}")
            source = chunk.get("source_title") or chunk.get("source") or ""
            if source:
                lines.append(f"Source: {source}")
            lines.append(chunk.get("chunk_content") or chunk.get("content") or "")

            # Graph relations for this chunk
            chunk_uuid = chunk.get("chunk_uuid") or chunk.get("id") or ""
            if chunk_uuid and chunk_id_to_group_ids and chunk_relations:
                group_ids = chunk_id_to_group_ids.get(chunk_uuid, [])
                relevant_relations = [
                    r for r in chunk_relations if r.get("group_id") in group_ids
                ]
                if relevant_relations:
                    lines.append("Graph Relations:")
                    for rel in relevant_relations:
                        for triplet in rel.get("triplets") or []:
                            src = (triplet.get("source") or {}).get("name", "")
                            tgt = (triplet.get("target") or {}).get("name", "")
                            rel_ = triplet.get("relation") or {}
                            pred = rel_.get("canonical_predicate", "")
                            ctx = rel_.get("context", "")
                            line = f"  [{src}] -> {pred} -> [{tgt}]: {ctx}"
                            temporal = rel_.get("temporal_details")
                            if temporal:
                                line += f" [Time: {temporal}]"
                            lines.append(line)

            # Extra context
            extra_ids = chunk.get("extra_context_ids") or []
            if extra_ids and additional_context:
                extras = [additional_context[eid] for eid in extra_ids if eid in additional_context]
                if extras:
                    lines.append("Extra Context:")
                    for extra in extras:
                        extra_source = extra.get("source_title", "")
                        extra_content = extra.get("chunk_content") or extra.get("content", "")
                        lines.append(f"  Related ({extra_source}): {extra_content}")

            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def _response_to_dict(resp) -> dict:
    """Convert a HydraDB SDK response object to a plain dict."""
    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    if hasattr(resp, "dict"):
        return resp.dict()
    if hasattr(resp, "__dict__"):
        return resp.__dict__
    return dict(resp)


# ── Recall wrappers ──────────────────────────────────────────────

def recall_jobs(query: str, max_results: int = 15, alpha: float = 0.8) -> dict:
    """Recall job postings from HydraDB using semantic + keyword search."""
    client = _get_client()
    try:
        result = client.recall.full_recall(
            query=query,
            tenant_id=TENANT_ID,
            sub_tenant_id="job_postings",
            alpha=alpha,
            recency_bias=0,
        )
        return _response_to_dict(result)
    except Exception as e:
        log.warning("[HYDRA] Job recall failed: %s", e)
        return {}


def recall_profiles(query: str, max_results: int = 10, alpha: float = 0.8) -> dict:
    """Recall applicant profiles from HydraDB."""
    client = _get_client()
    try:
        result = client.recall.full_recall(
            query=query,
            tenant_id=TENANT_ID,
            sub_tenant_id="applicant_profiles",
            alpha=alpha,
            recency_bias=0,
        )
        return _response_to_dict(result)
    except Exception as e:
        log.warning("[HYDRA] Profile recall failed: %s", e)
        return {}


def recall_all(query: str, alpha: float = 0.8) -> dict:
    """Recall across all sub-tenants."""
    client = _get_client()
    try:
        result = client.recall.full_recall(
            query=query,
            tenant_id=TENANT_ID,
            sub_tenant_id="",
            alpha=alpha,
            recency_bias=0,
        )
        return _response_to_dict(result)
    except Exception as e:
        log.warning("[HYDRA] Full recall failed: %s", e)
        return {}


# ── Parse job data from HydraDB chunks ───────────────────────────

def _extract_field(text: str, field_name: str) -> str | None:
    """Extract a field value from markdown-formatted chunk content."""
    import re
    pattern = rf"\*\*{re.escape(field_name)}:\*\*\s*(.+)"
    match = re.search(pattern, text)
    if match:
        return match.group(1).strip()
    return None


def _extract_section(text: str, section_name: str) -> str:
    """Extract content under a ## section header."""
    import re
    pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\n# |\Z)"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def parse_job_from_chunk(chunk: dict) -> dict | None:
    """Parse a job posting from a HydraDB chunk's content.
    Returns a dict matching the job_matcher format, or None if parsing fails.
    """
    content = chunk.get("chunk_content") or chunk.get("content") or ""
    if not content or "Job Posting:" not in content:
        return None

    import re

    title = _extract_field(content, "Job Posting") or ""
    # Try to get title from the # header
    title_match = re.search(r"# Job Posting:\s*(.+)", content)
    if title_match:
        title = title_match.group(1).strip()

    company = _extract_field(content, "Company") or "Horizon Technologies"
    department = _extract_field(content, "Department") or ""
    job_type = _extract_field(content, "Job Type") or "full_time"
    status = _extract_field(content, "Status") or "open"

    # Location
    location_str = _extract_field(content, "Location") or ""
    location = [l.strip() for l in location_str.split(",") if l.strip() and l.strip() != "N/A"]

    # Experience
    exp_str = _extract_field(content, "Experience Required") or ""
    exp_match = re.search(r"(\d+)\s*-\s*(\d+)", exp_str)
    exp_min = int(exp_match.group(1)) if exp_match else 0
    exp_max = int(exp_match.group(2)) if exp_match else 0

    # Salary
    salary_str = _extract_field(content, "Salary") or ""
    # e.g., "Salary: 9.0 LPA - 18.0 LPA (INR)"
    sal_match = re.search(r"([\d.]+)\s*LPA\s*-\s*([\d.]+)\s*LPA", salary_str)
    salary_min = float(sal_match.group(1)) * 100000 if sal_match else None
    salary_max = float(sal_match.group(2)) * 100000 if sal_match else None

    # Description
    description = _extract_section(content, "Description")

    # Skills
    req_skills_str = _extract_section(content, "Required Skills")
    required_skills = [s.strip() for s in req_skills_str.split(",") if s.strip()]

    pref_skills_str = _extract_section(content, "Preferred Skills")
    preferred_skills = [s.strip() for s in pref_skills_str.split(",") if s.strip()]

    # Job ID
    job_id_match = re.search(r"Job ID:\s*(\d+)", content)
    job_id = int(job_id_match.group(1)) if job_id_match else None

    if not title or not job_id:
        return None

    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "department": department,
        "description": description,
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "experience_min": exp_min,
        "experience_max": exp_max,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": "INR",
        "location": location,
        "job_type": job_type,
        "deadline": None,
        "posted_at": None,
        "relevance_score": round(float(chunk.get("relevancy_score") or 0), 3),
    }


def search_jobs_hydra(query: str, limit: int = 15) -> list[dict]:
    """Search jobs via HydraDB recall and parse results into job dicts."""
    result = recall_jobs(query, max_results=limit)
    chunks = result.get("chunks") or []

    jobs = []
    seen_ids = set()
    for chunk in chunks[:limit]:
        job = parse_job_from_chunk(chunk)
        if job and job["job_id"] not in seen_ids:
            seen_ids.add(job["job_id"])
            jobs.append(job)

    return jobs


def search_job_by_title_hydra(title_query: str) -> dict | None:
    """Search for a single job by title using HydraDB semantic recall."""
    result = recall_jobs(title_query, max_results=5, alpha=0.9)
    chunks = result.get("chunks") or []

    for chunk in chunks:
        job = parse_job_from_chunk(chunk)
        if job:
            return job
    return None
