"""Applicant Agent — LLM calls for the applicant pipeline.

Handles: info extraction, resume parsing, skill gap analysis,
cover letter generation, interview prep.
Follows the same _call_llm pattern as onboarding_agent.py.
"""

import json
import re
import logging
import time
import httpx
from core.config import config
from agents.prompts import (
    APPLICANT_EXTRACT_PROMPT, APPLICANT_RESUME_PARSE_PROMPT,
    APPLICANT_SKILL_GAP_PROMPT, APPLICANT_COVER_LETTER_PROMPT,
    APPLICANT_COVER_LETTER_REVISE_PROMPT, APPLICANT_INTERVIEW_PREP_PROMPT,
    APPLICANT_QUESTION_ANSWER_PROMPT,
    ROUTER_CLASSIFY_PROMPT, FORM_FILLER_PROMPT, INTERROGATOR_PROMPT,
    SKILL_CONFIRMATION_PROMPT,
)

log = logging.getLogger("applicant_agent")
MAX_RETRIES = 2


async def _call_llm(system_prompt: str, user_message: str, label: str = "APPLICANT") -> str:
    """Call OpenRouter. Same retry/strip pattern as onboarding_agent."""
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
                        "max_tokens": 16384,
                        "temperature": 0.1,
                        "top_p": 0.95,
                        "reasoning": {"effort": "none"},
                    },
                )
                elapsed = time.time() - t0
                response.raise_for_status()
                data = response.json()
                raw = data["choices"][0]["message"]["content"]

                # Strip <think>...</think> blocks
                raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                # Strip markdown code fences
                raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
                raw = re.sub(r"\n?```\s*$", "", raw)

                usage = data.get("usage", {})
                log.info("[%s] Response in %.1fs (tokens: %d prompt, %d completion)",
                         label, elapsed,
                         usage.get("prompt_tokens", 0),
                         usage.get("completion_tokens", 0))
                return raw.strip()
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_error = e
            log.warning("[%s] Attempt %d failed: %s", label, attempt + 1, e)
            if attempt < MAX_RETRIES:
                import asyncio
                await asyncio.sleep(2)
        except Exception as e:
            last_error = e
            log.error("[%s] Unexpected error: %s", label, e)
            break

    raise RuntimeError(f"[{label}] All attempts failed. Last error: {last_error}")


def _parse_json(raw: str) -> dict:
    """Extract JSON from LLM response."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Cannot parse JSON from response: {raw[:200]}")


async def extract_applicant_info(message: str) -> dict:
    """Extract profile info from applicant's message."""
    raw = await _call_llm(APPLICANT_EXTRACT_PROMPT, message, "EXTRACT")
    return _parse_json(raw)


async def parse_resume_text(resume_text: str) -> dict:
    """LLM parses raw resume text into structured profile data."""
    raw = await _call_llm(APPLICANT_RESUME_PARSE_PROMPT, resume_text, "RESUME_PARSE")
    return _parse_json(raw)


async def generate_skill_gap_plan(current_skills: list, missing_required: list,
                                   missing_preferred: list, target_role: str) -> dict:
    """LLM prioritizes skill gaps and produces a learning plan."""
    user_msg = json.dumps({
        "current_skills": current_skills,
        "missing_required": list(missing_required),
        "missing_preferred": list(missing_preferred),
        "target_role": target_role,
    })
    raw = await _call_llm(APPLICANT_SKILL_GAP_PROMPT, user_msg, "SKILL_GAP")
    return _parse_json(raw)


async def generate_cover_letter(profile: dict, job: dict) -> str:
    """Generate a tailored cover letter."""
    user_msg = json.dumps({"applicant_profile": profile, "job_posting": job})
    raw = await _call_llm(APPLICANT_COVER_LETTER_PROMPT, user_msg, "COVER_LETTER")
    return raw


async def revise_cover_letter(previous: str, feedback: str) -> str:
    """Revise a cover letter based on user feedback."""
    user_msg = f"Previous cover letter:\n{previous}\n\nUser feedback:\n{feedback}"
    raw = await _call_llm(APPLICANT_COVER_LETTER_REVISE_PROMPT, user_msg, "COVER_REVISE")
    return raw


async def generate_interview_prep(profile: dict, job: dict) -> dict:
    """Generate interview preparation content."""
    user_msg = json.dumps({"applicant_profile": profile, "job_posting": job})
    raw = await _call_llm(APPLICANT_INTERVIEW_PREP_PROMPT, user_msg, "INTERVIEW_PREP")
    return _parse_json(raw)


async def extract_question_answers(message: str, questions: list[dict]) -> dict:
    """Extract structured answers from applicant's free-text response to profile questions."""
    user_msg = json.dumps({"user_message": message, "questions_asked": questions})
    raw = await _call_llm(APPLICANT_QUESTION_ANSWER_PROMPT, user_msg, "QA_EXTRACT")
    return _parse_json(raw)


# ── Two-Agent Hiring Pipeline Functions ──────────────────────

async def classify_front_door(message: str) -> str:
    """Router LLM — classifies user intent as hiring/company_info/general. Fast call."""
    last_error = None
    for attempt in range(2):
        try:
            log.info("[ROUTER] Classifying front-door intent (attempt=%d)...", attempt + 1)
            t0 = time.time()
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.openrouter_model,
                        "messages": [
                            {"role": "system", "content": ROUTER_CLASSIFY_PROMPT},
                            {"role": "user", "content": message},
                        ],
                        "max_tokens": 20,
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"]
                raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip().lower()
                elapsed = time.time() - t0
                log.info("[ROUTER] Classified as '%s' in %.1fs", raw, elapsed)

                valid = {"hiring", "company_info", "general"}
                if raw in valid:
                    return raw
                for intent in valid:
                    if intent in raw:
                        return intent
                return "general"
        except Exception as e:
            last_error = e
            log.warning("[ROUTER] Attempt %d failed: %s", attempt + 1, e)
            import asyncio
            await asyncio.sleep(1)

    log.error("[ROUTER] All attempts failed, defaulting to 'general': %s", last_error)
    return "general"


async def fill_form(message: str, form_state: dict, context: list = None) -> dict:
    """Form-Filler LLM — extracts structured data from user message into form fields.
    Returns a JSON delta of fields to update.
    """
    prompt = FORM_FILLER_PROMPT.format(form_state=json.dumps(form_state, indent=2))

    # Include recent conversation context (last 6 messages)
    user_msg = message
    if context:
        recent = context[-6:]
        ctx_str = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in recent)
        user_msg = f"Recent conversation:\n{ctx_str}\n\nUser's latest message:\n{message}"

    raw = await _call_llm(prompt, user_msg, "FORM_FILLER")
    return _parse_json(raw)


async def interrogate(form_state: dict, missing_fields: list, history: list = None) -> dict:
    """Interrogator LLM — generates 1-2 natural conversational questions for missing fields.
    Returns {"message": "conversational text", "targeting_fields": [...]}.
    """
    prompt = INTERROGATOR_PROMPT.format(
        form_state=json.dumps(form_state, indent=2),
        missing_fields=", ".join(missing_fields),
    )

    # Include recent conversation for context
    user_msg = "Generate the next questions for the applicant."
    if history:
        recent = history[-6:]
        ctx_str = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in recent)
        user_msg = f"Conversation so far:\n{ctx_str}\n\nGenerate the next questions."

    raw = await _call_llm(prompt, user_msg, "INTERROGATOR")
    result = _parse_json(raw)
    # Ensure we have a message field
    if "message" not in result and "questions" in result:
        result["message"] = result["questions"]
    return result


async def confirm_skills(current_skills: list, missing_job_skills: list) -> dict:
    """Skill Confirmation LLM — asks user about commonly required skills they may have forgotten.
    Returns {"message": "question text", "skills_asked_about": [...]}.
    """
    prompt = SKILL_CONFIRMATION_PROMPT.format(
        current_skills=", ".join(current_skills) if current_skills else "none listed",
        missing_job_skills=", ".join(missing_job_skills),
    )
    raw = await _call_llm(prompt, "Ask the applicant about these skills.", "SKILL_CONFIRM")
    result = _parse_json(raw)
    if "message" not in result:
        # Fallback
        result["message"] = f"Before I show you jobs, I noticed many positions require: {', '.join(missing_job_skills)}. Do you have experience with any of these?"
        result["skills_asked_about"] = missing_job_skills
    return result


# ── LLM-based intent classification (used only in phase 4+) ──

INTENT_CLASSIFY_PROMPT = """You are a routing classifier for a job application assistant. Given the user's message and their current state, classify their intent into exactly ONE of these categories:

- apply_job: User wants to submit an application to a specific job (mentions a job number like #3 or a specific job title they've seen in the listings)
- find_jobs: User wants to search or browse more job listings
- interview_prep: User wants to prepare for an interview
- my_applications: User wants to see their application status or dashboard
- withdraw_application: User wants to withdraw, cancel, or retract an application they previously submitted
- show_profile: User wants to view or edit their profile
- edit_profile: User explicitly wants to edit or update specific profile fields (e.g., "change my phone", "update my skills", "edit my desired role")
- profile_info: User is sharing new personal info, skills, experience
- general: Greeting, thanks, or anything else

Current state: phase={phase}, profile_completion={completion}%

Output ONLY the intent name (one word), nothing else."""


async def classify_intent(message: str, phase: int, completion: int) -> str:
    """LLM classifies user intent. Fast call — max_tokens=20, temperature=0."""
    prompt = INTENT_CLASSIFY_PROMPT.format(phase=phase, completion=completion)
    last_error = None
    for attempt in range(2):
        try:
            log.info("[INTENT] Classifying intent (attempt=%d)...", attempt + 1)
            t0 = time.time()
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.openrouter_model,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": message},
                        ],
                        "max_tokens": 20,
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"]
                raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip().lower()
                elapsed = time.time() - t0
                log.info("[INTENT] Classified as '%s' in %.1fs", raw, elapsed)

                valid = {"apply_job", "find_jobs", "interview_prep", "my_applications",
                         "withdraw_application", "show_profile", "edit_profile", "profile_info", "general"}
                if raw in valid:
                    return raw
                # Try to find a valid intent in the response
                for intent in valid:
                    if intent in raw:
                        return intent
                return "general"
        except Exception as e:
            last_error = e
            log.warning("[INTENT] Attempt %d failed: %s", attempt + 1, e)
            import asyncio
            await asyncio.sleep(1)

    log.error("[INTENT] All attempts failed, defaulting to 'general': %s", last_error)
    return "general"
