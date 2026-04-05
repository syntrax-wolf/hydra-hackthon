# Applicant Agent — Complete Workflow Specification (v2)

## How This Document Relates to the Previous One

The previous document (`APPLICANT_PORTAL_README.md`) covered the **database schema, file structure, and API endpoints**. This document covers the **agent behavior** — what the AI agent actually does at each step, how it talks to the user, what data it collects, and how every feature works end-to-end.

### Key Design Principles (v2 Updates)

Three rules that apply to EVERY data-writing step in this system:

1. **Confirm before save** — The agent NEVER writes to the database silently. It always shows the user what it understood, lets them review, edit inline, and then explicitly confirm before anything is persisted.
2. **Raw file always preserved** — When a user uploads a resume (or any file), the original file is saved immediately to disk, regardless of whether parsing succeeds. The parsed data is a best-effort layer on top.
3. **Manual editing always available** — At any point, the user can open their profile, click on any field, and edit it directly through editable form cards — no need to talk to the agent.

---

## The Big Picture: Applicant Journey

```
┌─────────────────────────────────────────────────────────────────────┐
│                     APPLICANT JOURNEY (7 PHASES)                    │
│                                                                     │
│  ① ONBOARDING ──► ② PROFILE ──► ③ SKILL GAP ──► ④ JOB DISCOVERY  │
│   (first msg)     (2-3 Q's      (YouTube        (matched job       │
│                    at a time)     playlists)      cards)            │
│                       │                               │             │
│                       ▼                               ▼             │
│                  ⑤ APPLICATION ──► ⑥ INTERVIEW ──► ⑦ TRACKING      │
│                   (apply + cover    (prep +         (dashboard +    │
│                    letter)           mock Q's)       follow-ups)    │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  MY PROFILE (accessible anytime via header)                 │    │
│  │  Editable form cards — user can manually change any field   │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

The agent is **stateful per session**. It tracks which phase the applicant is in via the `applicant_profiles.onboarding_phase` field and the conversation history.

---

## The Confirm-Before-Save Pattern (Used Everywhere)

This pattern is used at every point where data is about to be written to the database. It applies to: first message extraction, resume parsing, profile question answers, profile edits, and application submission.

### How it works

```
User sends message / uploads file / answers questions
                    │
                    ▼
        ┌── LLM extracts data ──┐
        │  (structured JSON)     │
        └───────────┬────────────┘
                    ▼
        ┌── Agent shows CONFIRMATION CARD ──┐
        │                                    │
        │  "Here's what I understood:"       │
        │                                    │
        │  ┌─ Editable fields ────────────┐  │
        │  │ Desired Role: [Backend Dev ▼]│  │
        │  │ Experience:   [3 years    ▼] │  │
        │  │ Skills:       [Python ✕]     │  │
        │  │               [FastAPI ✕]    │  │
        │  │               [+ Add more]   │  │
        │  │ Location:     [Bangalore  ▼] │  │
        │  └──────────────────────────────┘  │
        │                                    │
        │  [✅ Save]  [✏️ Edit]  [❌ Cancel] │
        └───────────┬────────────────────────┘
                    │
          ┌────────┼────────┐
          ▼        ▼        ▼
        Save     Edit     Cancel
        to DB    inline   discard
                 fields   all data
                 then
                 re-confirm
```

### The confirmation card response format

Every confirmation card has the same JSON structure:

```json
{
  "type": "applicant",
  "step": "confirm_data",
  "context": "profile_building",
  "message": "Here's what I understood from your message — please review:",
  "extracted_data": {
    "profile": {
      "desired_role": {"value": "Backend Developer", "editable": true, "input_type": "text"},
      "experience_years": {"value": 3, "editable": true, "input_type": "number"},
      "location_preference": {"value": ["Bangalore", "Remote"], "editable": true, "input_type": "multi_select", "options": ["Mumbai", "Delhi", "Bangalore", "Hyderabad", "Chennai", "Pune", "Kolkata", "Remote"]},
      "salary_min": {"value": 1800000, "editable": true, "input_type": "number", "label": "Min Salary (₹/year)"},
      "salary_max": {"value": 2500000, "editable": true, "input_type": "number", "label": "Max Salary (₹/year)"}
    },
    "skills": {
      "values": [
        {"skill_name": "Python", "proficiency_level": "advanced", "editable": true, "removable": true},
        {"skill_name": "FastAPI", "proficiency_level": "intermediate", "editable": true, "removable": true},
        {"skill_name": "PostgreSQL", "proficiency_level": "intermediate", "editable": true, "removable": true}
      ],
      "allow_add": true
    },
    "education": {
      "values": [
        {"institution": "IIT Delhi", "degree": "B.Tech", "field": "Electrical Engineering", "end_year": 2025, "editable": true, "removable": true}
      ],
      "allow_add": true
    },
    "experience": {
      "values": [
        {"company_name": "TechCorp", "role_title": "Python Developer", "start_date": "2023-06", "end_date": null, "is_current": true, "editable": true, "removable": true}
      ],
      "allow_add": true
    }
  },
  "actions": ["save", "cancel"],
  "progress": {
    "completed": ["basic_info", "skills", "education", "experience"],
    "remaining": ["resume", "preferences"],
    "percentage": 70
  }
}
```

### Frontend rendering of the confirmation card

```
┌──────────────────────────────────────────────────────────────────┐
│  📋 Here's what I understood — please review and edit:           │
│                                                                   │
│  👤 Profile                                                      │
│  ┌────────────────────────────────────────────────────────┐      │
│  │ Desired Role    [Backend Developer          ] ✏️      │      │
│  │ Experience      [3                ] years     ✏️      │      │
│  │ Location        [Bangalore ✕] [Remote ✕] [+ Add]     │      │
│  │ Salary Range    [₹18,00,000 ] to [₹25,00,000 ] ✏️   │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                   │
│  🛠️ Skills                                                       │
│  ┌────────────────────────────────────────────────────────┐      │
│  │ Python       [Advanced     ▼]              [✕ Remove] │      │
│  │ FastAPI      [Intermediate ▼]              [✕ Remove] │      │
│  │ PostgreSQL   [Intermediate ▼]              [✕ Remove] │      │
│  │                                                        │      │
│  │ [+ Add Skill: _____________________ ]                  │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                   │
│  🎓 Education                                                    │
│  ┌────────────────────────────────────────────────────────┐      │
│  │ IIT Delhi · B.Tech · Electrical Engineering · 2025     │      │
│  │                                      [✏️ Edit] [✕]    │      │
│  │                                                        │      │
│  │ [+ Add Education]                                      │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                   │
│  💼 Experience                                                    │
│  ┌────────────────────────────────────────────────────────┐      │
│  │ Python Developer at TechCorp (Jun 2023 – Present)      │      │
│  │                                      [✏️ Edit] [✕]    │      │
│  │                                                        │      │
│  │ [+ Add Experience]                                     │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                   │
│  ┌─────────────────────────────────┐                             │
│  │  ▓▓▓▓▓▓▓░░░ 70%  Profile       │                             │
│  └─────────────────────────────────┘                             │
│                                                                   │
│     [✅ Looks Good, Save]              [❌ Cancel, Don't Save]   │
└──────────────────────────────────────────────────────────────────┘
```

**Every field is directly editable in the card itself.** The user can:
- Click on any text field and type a new value.
- Change dropdowns (proficiency level, location).
- Remove entries with the ✕ button.
- Add new skills, education, or experience entries with the + button.
- Then click "Save" to persist everything, or "Cancel" to discard.

**No separate "Edit mode" is needed.** The confirmation card IS the edit form.

### What happens on each action

**Save** → `POST /api/applicant/profile/confirm` with the (possibly modified) data → writes to DB → agent responds with acknowledgment + next questions for missing fields.

**Cancel** → Nothing is written. Agent responds: *"No problem, nothing saved. Want to try again or move on?"*

---

## Phase 1: First Contact — Extract What You Can

### What triggers it
The applicant just registered and sends their first message.

### What the agent does

**Step 1.1 — LLM extraction from the first message**

Call `extract_applicant_info()` — same pattern as `extract_employee_info()` in `agents/onboarding_agent.py`.

System prompt:

```
You are a career assistant AI. Extract whatever information the applicant has 
shared about themselves from their message. Output ONLY valid JSON.

Extract these fields (use null for anything not mentioned):
{
  "desired_role": "what kind of job they want",
  "desired_department": "engineering|data_science|design|marketing|sales|finance|product|hr or null",
  "skills_mentioned": [{"skill_name": "Python", "proficiency_level": "advanced"}, ...] or [],
  "experience_years": number or null,
  "current_company": "string or null",
  "current_role": "string or null",
  "education": {"institution": "...", "degree": "...", "field": "...", "year": ...} or null,
  "location_preference": ["Bangalore", "Remote", ...] or [],
  "salary_expectation": {"min": number, "max": number, "currency": "INR"} or null,
  "job_type_preference": ["full_time", "internship", ...] or [],
  "willing_to_relocate": true/false/null
}

If the user mentions a proficiency context (e.g., "expert in Python", "some React"), 
infer the proficiency_level. If not mentioned, default to "intermediate".
```

**Step 1.2 — Show confirmation card (DO NOT save yet)**

The agent takes whatever the LLM extracted and returns it as a **confirmation card** (described in the pattern above). The user reviews, edits inline if needed, and clicks Save.

**Step 1.3 — On Save, determine what's still missing and ask 2-3 questions**

After saving, compare the profile against required sections:

```python
PROFILE_SECTIONS = {
    "basic":      ["desired_role", "experience_years", "location_preference"],
    "skills":     ["skills_mentioned"],       # need at least 3 skills
    "education":  ["institution", "degree"],
    "experience": ["current_or_last_role"],   # at least 1 entry
    "resume":     ["resume_file"],            # uploaded file
    "preferences":["salary_expectation", "job_type_preference"],
}
```

The agent picks the **2-3 most important missing items** and asks in a single message. It does NOT dump all questions at once.

### Example flow (first message was rich)

User types: *"I'm a Python developer with 3 years at TechCorp, looking for backend roles in Bangalore, 18-25 LPA"*

**Agent response 1** — Confirmation card showing:
- Desired Role: Backend Developer
- Experience: 3 years
- Skills: Python (advanced)
- Experience: Python Developer at TechCorp (current)
- Location: Bangalore
- Salary: ₹18-25 LPA
- [Save] [Cancel]

**User clicks Save** (or edits a field first, then saves).

**Agent response 2** — Acknowledgment + next questions:
```json
{
  "type": "applicant",
  "step": "profile_building",
  "message": "Saved! Your profile is off to a great start. A few more things to strengthen it:",
  "questions": [
    {
      "id": "skills_detail",
      "text": "What other technologies and tools do you work with? (e.g., Django, PostgreSQL, Docker, AWS, Redis)",
      "field": "skills",
      "input_type": "text"
    },
    {
      "id": "education",
      "text": "What's your educational background? (e.g., B.Tech CS from XYZ University, 2022)",
      "field": "education",
      "input_type": "text"
    },
    {
      "id": "resume_upload",
      "text": "Have a resume to upload? It helps me match you more accurately.",
      "field": "resume",
      "input_type": "file_upload"
    }
  ],
  "progress": { "completed": ["basic_info", "experience", "preferences"], "remaining": ["skills_detail", "education", "resume"], "percentage": 55 }
}
```

### Example flow (first message was vague)

User types: *"Hi, I need a job"*

Nothing to extract → no confirmation card needed → agent goes straight to questions:

```json
{
  "type": "applicant",
  "step": "profile_building",
  "message": "Welcome! I'll help you find the right job. Let me learn a bit about you first:",
  "questions": [
    { "id": "desired_role", "text": "What kind of role are you looking for? (e.g., Software Developer, Data Analyst, Designer)", "input_type": "text" },
    { "id": "experience", "text": "How many years of work experience do you have? (0 if you're a fresher)", "input_type": "text" },
    { "id": "skills", "text": "What are your top skills? (e.g., Python, Excel, Figma, SQL)", "input_type": "text" }
  ],
  "progress": { "completed": [], "remaining": ["basic_info", "skills", "experience", "education", "resume", "preferences"], "percentage": 5 }
}
```

When the user answers these questions, their response goes through the same cycle: LLM extracts → confirmation card → user reviews/edits → Save → next questions.

---

## Phase 2: Iterative Profile Completion

### How it works

After each confirm-and-save cycle, the agent:
1. Recalculates the profile completion percentage.
2. If critical fields are still missing, asks 2-3 more questions.
3. When profile is ≥70% complete (has: desired role, ≥3 skills, experience level, at least 1 education or experience entry), it **moves to Phase 3** (skill gap analysis).

### The question flow (2-3 at a time)

**Round 1** (if first message was vague):
- Desired role + experience years + top skills

**Round 2**:
- Education background + current/last company & role + location preference

**Round 3**:
- Resume upload + salary expectations + job type preference (full-time/contract/internship)

**Round 4** (optional, if still thin):
- Certifications + projects + LinkedIn/GitHub URLs

The agent **never asks more than 3 questions at once** and always acknowledges what it saved before asking more. **Every answer goes through the confirmation card flow** — extract → show → edit → save.

### Resume processing — Robust PDF handling

When the user uploads a resume, the system follows a strict order:

**Step A — ALWAYS save the raw PDF first (before any parsing)**

```python
import shutil
from pathlib import Path

def save_raw_resume(applicant_id: int, uploaded_file) -> str:
    """Save the original PDF to disk. This ALWAYS succeeds or raises.
    The raw file is preserved regardless of parsing outcome."""
    
    resume_dir = Path(config.resume_upload_dir) / str(applicant_id)
    resume_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"resume_{timestamp}.pdf"
    filepath = resume_dir / filename
    
    # Save the raw bytes — no processing
    with open(filepath, "wb") as f:
        shutil.copyfileobj(uploaded_file.file, f)
    
    # Update DB immediately — the file path is now recorded
    execute_write(
        "UPDATE applicant.applicant_profiles SET resume_file_path = %s, resume_updated_at = %s WHERE applicant_id = %s",
        [str(filepath), datetime.now(), applicant_id],
    )
    
    log.info("[RESUME] Raw PDF saved: %s (%d bytes)", filepath, filepath.stat().st_size)
    return str(filepath)
```

**Step B — Attempt text extraction with multi-strategy fallback**

```python
async def extract_resume_text(filepath: str) -> tuple[str, str]:
    """Try multiple extraction strategies. Returns (text, method_used).
    Never raises — returns ("", "failed") on complete failure."""
    
    # Strategy 1: pdfplumber (best for text PDFs with tables/columns)
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text += page_text + "\n"
        if len(text.strip()) > 100:  # reasonable amount of text extracted
            return text.strip(), "pdfplumber"
    except Exception as e:
        log.warning("[RESUME] pdfplumber failed: %s", e)
    
    # Strategy 2: PyPDF2 (simpler, handles some PDFs pdfplumber can't)
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
        if len(text.strip()) > 100:
            return text.strip(), "pypdf2"
    except Exception as e:
        log.warning("[RESUME] PyPDF2 failed: %s", e)
    
    # Strategy 3: OCR via pytesseract (for scanned/image PDFs)
    try:
        from pdf2image import convert_from_path
        import pytesseract
        images = convert_from_path(filepath, dpi=300)
        text = ""
        for img in images:
            text += pytesseract.image_to_string(img) + "\n"
        if len(text.strip()) > 50:
            return text.strip(), "ocr"
    except Exception as e:
        log.warning("[RESUME] OCR failed: %s", e)
    
    # All strategies failed
    log.error("[RESUME] All extraction strategies failed for %s", filepath)
    return "", "failed"
```

**Step C — If text was extracted, LLM parses it into structured data**

```python
async def parse_resume_text(resume_text: str) -> dict:
    """LLM extracts structured profile data from resume text."""
    # Returns:
    # {
    #   "skills": [{"skill_name": "Python", "proficiency_level": "advanced", "years": 3}, ...],
    #   "education": [{"institution": "IIT Delhi", "degree": "B.Tech", "field": "CS", "end_year": 2022}],
    #   "experience": [{"company": "TechCorp", "role": "SDE", "start": "2022-06", "end": null, "current": true, "description": "..."}],
    #   "certifications": ["AWS Solutions Architect"],
    #   "projects": [{"name": "ChatBot", "description": "...", "tech": ["Python", "LangChain"]}],
    #   "summary": "Experienced backend developer with..."
    # }
```

**Step D — Show confirmation card with parsed data (DO NOT save yet)**

If parsing succeeded (any method), the agent shows a confirmation card with all extracted data — fully editable. This is critical because LLM parsing is imperfect, especially for complex resumes.

```json
{
  "type": "applicant",
  "step": "confirm_data",
  "context": "resume_parsed",
  "message": "I extracted the following from your resume — please review and correct anything that looks off:",
  "resume_status": {
    "raw_file_saved": true,
    "file_path": "resume_20260404_143022.pdf",
    "extraction_method": "pdfplumber",
    "parsing_confidence": "high"
  },
  "extracted_data": {
    "skills": {
      "values": [
        {"skill_name": "Python", "proficiency_level": "advanced", "years_of_experience": 3, "editable": true, "removable": true},
        {"skill_name": "Django", "proficiency_level": "intermediate", "years_of_experience": 2, "editable": true, "removable": true},
        {"skill_name": "PostgreSQL", "proficiency_level": "intermediate", "years_of_experience": 2, "editable": true, "removable": true},
        {"skill_name": "Docker", "proficiency_level": "beginner", "years_of_experience": 1, "editable": true, "removable": true}
      ],
      "allow_add": true
    },
    "education": {
      "values": [
        {"institution": "IIT Delhi", "degree": "B.Tech", "field": "Computer Science", "end_year": 2022, "editable": true, "removable": true}
      ],
      "allow_add": true
    },
    "experience": {
      "values": [
        {"company_name": "TechCorp", "role_title": "Software Developer", "start_date": "2022-07", "end_date": null, "is_current": true, "description": "Built REST APIs and microservices...", "editable": true, "removable": true},
        {"company_name": "StartupABC", "role_title": "Intern", "start_date": "2022-01", "end_date": "2022-06", "is_current": false, "description": "Frontend development with React...", "editable": true, "removable": true}
      ],
      "allow_add": true
    }
  },
  "actions": ["save", "cancel"]
}
```

**Step E — If extraction FAILED, the agent handles it gracefully**

```json
{
  "type": "applicant",
  "step": "resume_upload_result",
  "message": "I saved your resume, but I couldn't read it automatically (it might be a scanned document or have a complex layout). No worries — your original file is safely stored and will be attached to all your applications. Let me ask you a few questions to fill in your profile instead:",
  "resume_status": {
    "raw_file_saved": true,
    "file_path": "resume_20260404_143022.pdf",
    "extraction_method": "failed",
    "parsing_confidence": "none"
  },
  "questions": [
    { "id": "skills", "text": "What are your main technical skills?", "input_type": "text" },
    { "id": "experience", "text": "Where do you currently work, and what's your role?", "input_type": "text" },
    { "id": "education", "text": "What's your educational background?", "input_type": "text" }
  ]
}
```

**The raw PDF is ALWAYS available for:**
- Attaching to job applications (copied as `resume_snapshot_path`).
- Manual review by the company (downloaded via `/api/download/`).
- Re-parsing later if the system improves.

### Merge logic (resume data vs existing profile data)

When the user confirms resume-parsed data, the system merges it with any data already in the profile:

```python
def merge_profile_data(existing: dict, from_resume: dict) -> dict:
    """Merge resume-extracted data into existing profile.
    Rule: existing user-confirmed data takes priority over resume extraction.
    Resume data only fills NULL/empty fields."""
    
    merged = {}
    for key in from_resume:
        existing_val = existing.get(key)
        resume_val = from_resume.get(key)
        
        if existing_val is None or existing_val == "" or existing_val == []:
            # Empty in existing → use resume data
            merged[key] = resume_val
        else:
            # Already has user-confirmed data → keep it
            merged[key] = existing_val
    
    # For list fields (skills, education, experience): merge without duplicates
    # Resume can ADD entries that don't already exist
    for list_key in ["skills", "education", "experience"]:
        existing_list = existing.get(list_key, [])
        resume_list = from_resume.get(list_key, [])
        # Add resume entries that aren't already present (by name/institution/company)
        merged[list_key] = _merge_lists(existing_list, resume_list, list_key)
    
    return merged
```

### Transition to Phase 3

Once profile is ≥70% complete, the agent responds:

```json
{
  "type": "applicant",
  "step": "profile_complete",
  "message": "Your profile is looking solid! Here's a quick summary:",
  "profile_summary": {
    "name": "Anmol Sharma",
    "headline": "Backend Developer — Python, FastAPI, PostgreSQL",
    "experience": "3 years at TechCorp",
    "education": "B.Tech EE, IIT Delhi (2025)",
    "top_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "Redis"],
    "looking_for": "Backend SDE roles in Bangalore/Remote, ₹18-25 LPA",
    "resume": "resume_20260404.pdf (uploaded)",
    "completion": 82
  },
  "next_step": "skill_gap_analysis",
  "message_continued": "Now let me analyze your skills against the jobs you're targeting and suggest some resources to fill any gaps. Ready?",
  "follow_ups": [
    "Yes, analyze my skill gaps",
    "Skip this, show me jobs directly",
    "Let me edit my profile first"
  ]
}
```

---

## My Profile — Manual Editing (Accessible Anytime)

### Access points

The user can access their full editable profile at any time through:
1. **Header link** — "My Profile" button in the top navigation bar.
2. **Chat command** — typing *"edit my profile"*, *"update my skills"*, *"change my salary"*, *"show my profile"*.
3. **Follow-up button** — "Let me edit my profile first" shown at various stages.

### API endpoint

```
GET  /api/applicant/profile          → returns full profile with all sections
PUT  /api/applicant/profile          → updates profile fields
POST /api/applicant/profile/skills   → add a skill
DELETE /api/applicant/profile/skills/{id}  → remove a skill
PUT  /api/applicant/profile/skills/{id}    → update a skill
POST /api/applicant/profile/education      → add education entry
PUT  /api/applicant/profile/education/{id} → update education entry
DELETE /api/applicant/profile/education/{id} → remove education entry
POST /api/applicant/profile/experience       → add experience entry
PUT  /api/applicant/profile/experience/{id}  → update experience entry
DELETE /api/applicant/profile/experience/{id} → remove experience entry
POST /api/applicant/profile/resume/upload    → upload new resume
DELETE /api/applicant/profile/resume         → remove resume
```

### Profile page layout — All sections are editable form cards

The profile page is NOT a read-only display. Every section is a **live editable form card**. The user can click any field, change it, and save — no need to enter an "edit mode".

```
┌──────────────────────────────────────────────────────────────────┐
│  👤 My Profile                              Completion: 82% ▓▓▓▓│
│                                                                   │
│  ┌─ Basic Info ──────────────────────────────────────────────┐   │
│  │                                                            │   │
│  │  Full Name       [Anmol Sharma                  ] ✏️      │   │
│  │  Headline        [Backend Developer — Python, Fa] ✏️      │   │
│  │  Email           anmol@email.com (cannot change)          │   │
│  │  Phone           [+91-98765-43210               ] ✏️      │   │
│  │  Experience      [3       ] years                 ✏️      │   │
│  │  Current Company [TechCorp                      ] ✏️      │   │
│  │  Current Role    [Python Developer              ] ✏️      │   │
│  │  Location        [Bangalore                     ] ✏️      │   │
│  │  Willing to      [✓] Relocate                             │   │
│  │  Relocate                                                  │   │
│  │                                                            │   │
│  │                                       [Save Changes]       │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─ Preferences ─────────────────────────────────────────────┐   │
│  │                                                            │   │
│  │  Looking For     [Backend Developer roles       ] ✏️      │   │
│  │  Job Type        [Full-time ✓] [Contract ✗] [Intern ✗]   │   │
│  │  Locations       [Bangalore ✕] [Remote ✕] [+ Add]        │   │
│  │  Salary Range    [₹18,00,000 ] to [₹25,00,000 ]          │   │
│  │                                                            │   │
│  │                                       [Save Changes]       │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─ Skills ──────────────────────────────────────────────────┐   │
│  │                                                            │   │
│  │  Python       [Advanced     ▼]  [3] yrs       [✕ Remove] │   │
│  │  FastAPI      [Intermediate ▼]  [2] yrs       [✕ Remove] │   │
│  │  PostgreSQL   [Intermediate ▼]  [2] yrs       [✕ Remove] │   │
│  │  Docker       [Beginner     ▼]  [1] yrs       [✕ Remove] │   │
│  │  Redis        [Intermediate ▼]  [2] yrs       [✕ Remove] │   │
│  │                                                            │   │
│  │  ┌─ Add Skill ─────────────────────────────────┐          │   │
│  │  │ Skill: [____________] Level: [Beginner ▼]   │          │   │
│  │  │ Years: [__]              [+ Add]             │          │   │
│  │  └──────────────────────────────────────────────┘          │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─ Education ───────────────────────────────────────────────┐   │
│  │                                                            │   │
│  │  ┌──────────────────────────────────────────────┐         │   │
│  │  │ 🎓 IIT Delhi                                  │         │   │
│  │  │    B.Tech · Electrical Engineering · 2025     │         │   │
│  │  │    GPA: [8.5      ]                           │         │   │
│  │  │                          [✏️ Edit] [✕ Delete] │         │   │
│  │  └──────────────────────────────────────────────┘         │   │
│  │                                                            │   │
│  │  [+ Add Education]                                        │   │
│  │  ┌─ New Entry ────────────────────────────────┐           │   │
│  │  │ Institution: [________________________________]        │   │
│  │  │ Degree:      [________________________________]        │   │
│  │  │ Field:       [________________________________]        │   │
│  │  │ Start Year:  [____]  End Year: [____]                  │   │
│  │  │ GPA/Grade:   [________]                                │   │
│  │  │                    [Save Entry]  [Cancel]              │   │
│  │  └────────────────────────────────────────────┘           │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─ Experience ──────────────────────────────────────────────┐   │
│  │                                                            │   │
│  │  ┌──────────────────────────────────────────────┐         │   │
│  │  │ 💼 TechCorp — Python Developer                │         │   │
│  │  │    Jun 2023 – Present (current)               │         │   │
│  │  │    Built REST APIs and microservices for...    │         │   │
│  │  │                          [✏️ Edit] [✕ Delete] │         │   │
│  │  └──────────────────────────────────────────────┘         │   │
│  │                                                            │   │
│  │  ┌──────────────────────────────────────────────┐         │   │
│  │  │ 💼 StartupABC — Intern                        │         │   │
│  │  │    Jan 2022 – Jun 2022                        │         │   │
│  │  │    Frontend development with React...          │         │   │
│  │  │                          [✏️ Edit] [✕ Delete] │         │   │
│  │  └──────────────────────────────────────────────┘         │   │
│  │                                                            │   │
│  │  [+ Add Experience]                                       │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─ Resume ──────────────────────────────────────────────────┐   │
│  │                                                            │   │
│  │  📄 resume_20260404.pdf (uploaded Apr 4, 2026)             │   │
│  │  [📥 Download]  [🔄 Upload New]  [✕ Remove]              │   │
│  │                                                            │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─ Links ───────────────────────────────────────────────────┐   │
│  │                                                            │   │
│  │  LinkedIn   [https://linkedin.com/in/anmol     ] ✏️      │   │
│  │  GitHub     [https://github.com/anmol          ] ✏️      │   │
│  │  Portfolio  [__________________________________ ] ✏️      │   │
│  │                                                            │   │
│  │                                       [Save Changes]       │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│                                          [← Back to Chat]        │
└──────────────────────────────────────────────────────────────────┘
```

### How edits work (frontend → backend)

Each section has its own **Save Changes** button. When clicked:

1. Frontend collects only the changed fields from that section.
2. Sends a `PUT` to the appropriate endpoint (e.g., `PUT /api/applicant/profile` for basic info, `PUT /api/applicant/profile/skills/3` for a specific skill).
3. Backend validates, updates the DB, and re-computes the profile embedding.
4. Frontend shows a brief "Saved ✓" confirmation inline (no modal, no page reload).

For add/delete operations on list items (skills, education, experience):
- **Add** — Expands an inline form under the section, user fills in, clicks "Save Entry".
- **Edit** — The card switches to editable fields in-place, user modifies, clicks "Save".
- **Delete** — Shows a brief "Are you sure?" inline confirmation, then removes.

### Chat-based profile editing

The user can also edit via the chat. If they type *"change my salary expectation to 20-30 LPA"* or *"add Kubernetes to my skills"*:

1. LLM parses the intent and extracts the change.
2. Agent shows a **mini confirmation card** for just that change:

```
┌──────────────────────────────────────────────────┐
│  ✏️ Profile Update                               │
│                                                    │
│  Salary Range: ₹18-25 LPA → [₹20,00,000] to     │
│                               [₹30,00,000]        │
│                                                    │
│           [✅ Save]        [❌ Cancel]              │
└──────────────────────────────────────────────────┘
```

or for adding a skill:

```
┌──────────────────────────────────────────────────┐
│  ✏️ Adding Skill                                  │
│                                                    │
│  Skill:       [Kubernetes               ]         │
│  Proficiency: [Beginner            ▼]             │
│  Years:       [0                      ]           │
│                                                    │
│           [✅ Add]         [❌ Cancel]              │
└──────────────────────────────────────────────────┘
```

Same confirm-before-save pattern, just a smaller card.

### Embedding update on profile changes

Whenever the profile is modified (via form OR chat), the backend triggers a background re-computation of the `profile_embedding`:

```python
async def update_profile_embedding(applicant_id: int):
    """Re-compute BGE-M3 embedding from current profile state."""
    profile = get_full_profile(applicant_id)
    skills = get_applicant_skills(applicant_id)
    experience = get_applicant_experience(applicant_id)
    
    text_for_embedding = " ".join([
        profile.get("headline", ""),
        profile.get("summary", ""),
        profile.get("desired_role", ""),
        " ".join(s["skill_name"] for s in skills),
        " ".join(f"{e['role_title']} at {e['company_name']}" for e in experience),
    ])
    
    embedding = await compute_bge_m3_embedding(text_for_embedding)
    
    execute_write(
        "UPDATE applicant.applicant_profiles SET profile_embedding = %s, updated_at = %s WHERE applicant_id = %s",
        [embedding, datetime.now(), applicant_id],
    )
```

This ensures that job matching always uses the latest profile data.

---

## Phase 3: Skill Gap Analysis + YouTube Recommendations

### What triggers it
Automatically after profile completion, or anytime the user asks *"what skills should I learn"*, *"how do I improve my chances"*, *"any courses for me"*.

### Step 3.1 — Identify target roles and their requirements

The agent fetches the top 5 matching open jobs from `applicant.job_postings` (using the profile embedding) and aggregates their `required_skills` and `preferred_skills`:

```python
target_jobs = semantic_search(applicant.profile_embedding, job_postings, top_k=5)
all_required = set()
all_preferred = set()
for job in target_jobs:
    all_required.update(job.required_skills)
    all_preferred.update(job.preferred_skills)

applicant_skills = get_applicant_skills(applicant_id)
missing_required = all_required - applicant_skills    # CRITICAL gaps
missing_preferred = all_preferred - applicant_skills  # NICE-TO-HAVE gaps
```

### Step 3.2 — LLM prioritizes the gaps

```python
async def generate_skill_gap_plan(current_skills, missing_required, missing_preferred, target_role) -> dict:
    # Returns:
    # {
    #   "critical_gaps": [
    #     {"skill": "Kubernetes", "why": "4 out of 5 target jobs require it", "priority": 1},
    #     {"skill": "AWS", "why": "3 out of 5 jobs need cloud experience", "priority": 2}
    #   ],
    #   "nice_to_have": [{"skill": "GraphQL", "why": "Trending in 2 job descriptions", "priority": 3}],
    #   "your_strengths": ["Python", "PostgreSQL", "Docker"],
    #   "overall_readiness": 72
    # }
```

### Step 3.3 — YouTube API search for each gap

For each skill gap (critical first, then nice-to-have), call the **YouTube Data API v3**:

```python
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

async def search_youtube(query: str, max_results: int = 3) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        # Playlists first (structured learning)
        playlist_resp = await client.get(YOUTUBE_SEARCH_URL, params={
            "part": "snippet",
            "q": f"{query} full course tutorial",
            "type": "playlist",
            "maxResults": max_results,
            "order": "relevance",
            "key": config.youtube_api_key,
        })
        playlists = _parse_results(playlist_resp.json(), "playlist")

        # Then individual videos
        video_resp = await client.get(YOUTUBE_SEARCH_URL, params={
            "part": "snippet",
            "q": f"{query} tutorial for beginners",
            "type": "video",
            "maxResults": max_results,
            "order": "relevance",
            "videoDuration": "long",
            "key": config.youtube_api_key,
        })
        videos = _parse_results(video_resp.json(), "video")
        return playlists + videos


async def get_recommendations_for_gaps(skill_gaps: list[dict]) -> list[dict]:
    recommendations = []
    for gap in skill_gaps[:5]:  # limit to top 5 to avoid API quota burn
        resources = await search_youtube(gap["skill"])
        recommendations.append({
            "skill": gap["skill"],
            "priority": gap.get("priority", 99),
            "why": gap.get("why", ""),
            "playlists": [r for r in resources if r["type"] == "playlist"][:2],
            "videos": [r for r in resources if r["type"] == "video"][:2],
        })
    return recommendations
```

### Step 3.4 — Response with YouTube cards

```
┌──────────────────────────────────────────────────────────────┐
│  📊 Skill Gap Analysis — Backend Developer Roles             │
│  ┌─────────────────────────────────────────────────┐         │
│  │  Overall Readiness: ▓▓▓▓▓▓▓░░░ 72%             │         │
│  └─────────────────────────────────────────────────┘         │
│                                                              │
│  ✅ Your Strengths                                           │
│  Python (5/5 jobs) · PostgreSQL (4/5) · Docker (3/5)        │
│                                                              │
│  ⚠️ Critical Gaps                                           │
│  ┌──────────────────────────────────────────────────┐        │
│  │  🔴 #1 Kubernetes — 4/5 jobs require this        │        │
│  │  📺 Recommended:                                  │        │
│  │  ┌────────────────────────────────────────┐       │        │
│  │  │ 🎬 ▶ Kubernetes Full Course            │       │        │
│  │  │    TechWorld with Nana · Playlist      │       │        │
│  │  └────────────────────────────────────────┘       │        │
│  │  ┌────────────────────────────────────────┐       │        │
│  │  │ 🎬 ▶ K8s Tutorial for Beginners        │       │        │
│  │  │    FreeCodeCamp · 2hr Video            │       │        │
│  │  └────────────────────────────────────────┘       │        │
│  └──────────────────────────────────────────────────┘        │
│  ┌──────────────────────────────────────────────────┐        │
│  │  🟡 #2 AWS — 3/5 jobs need cloud experience      │        │
│  │  📺 [playlist] [video]                            │        │
│  └──────────────────────────────────────────────────┘        │
│                                                              │
│  [Show me matching jobs →]  [Edit my profile]                │
└──────────────────────────────────────────────────────────────┘
```

---

## Phase 4: Job Discovery — Matched Job Cards

Unchanged from v1 — see previous document for full details. Summary:

1. Hybrid search (BGE-M3 dense + tsvector sparse) against open job postings.
2. Compute per-job match score (semantic + skill overlap + experience fit + salary fit).
3. Return 10-15 collapsed cards. User scrolls, clicks Expand to see full details.
4. Each card has [Apply] [Save] [Expand ▼] buttons.

---

## Phase 5: Application Submission

### Same confirm-before-save pattern

When the user clicks Apply or says *"apply to job #42"*:

1. Pre-checks (existing application? deadline? job open?).
2. LLM generates match analysis + tailored cover letter.
3. Agent shows **application preview** — a confirmation card:

```
┌──────────────────────────────────────────────────────────────┐
│  📝 Application Preview — Review before submitting           │
│                                                              │
│  🏢 Senior Backend Engineer at TechCorp                      │
│  📊 Match Score: 87%                                         │
│                                                              │
│  ✅ Strengths: Python (3yr), PostgreSQL, FastAPI             │
│  ❌ Gaps: Kubernetes, AWS                                     │
│                                                              │
│  📄 Resume: resume_20260404.pdf                              │
│         [🔄 Upload Different Resume]                          │
│                                                              │
│  ✉️ Cover Letter:                                            │
│  ┌────────────────────────────────────────────────────┐      │
│  │ Dear Hiring Team at TechCorp,                      │      │
│  │                                                     │      │
│  │ I am writing to express my interest in the Senior  │      │
│  │ Backend Engineer position. With 3 years of Python  │      │
│  │ development experience at TechCorp, I have built   │      │
│  │ scalable REST APIs and...                          │      │
│  │                                                     │      │
│  │ [This is a live editable text area — user can      │      │
│  │  directly modify the cover letter here]            │      │
│  └────────────────────────────────────────────────────┘      │
│                                                              │
│  [✅ Submit Application]  [✏️ Revise Cover Letter]           │
│  [❌ Cancel]                                                  │
└──────────────────────────────────────────────────────────────┘
```

The user can:
- **Edit the cover letter directly** in the text area.
- **Ask for a revision** via chat: *"make it more formal"* or *"mention my open-source work"*.
- **Upload a different resume** for this specific application.
- **Submit** — creates the application record + timeline event.
- **Cancel** — nothing saved.

---

## Phase 6: Interview Preparation

Unchanged from v1 — see previous document. Summary:

1. LLM generates company research, role analysis, 10-15 sample questions with tips, skill gap advice.
2. Cached in `interview_prep` table.
3. Questions shown as expandable cards with hidden tips.

---

## Phase 7: Application Tracking + Follow-ups

Unchanged from v1 — see previous document. Summary:

1. Dashboard showing all applications grouped by status.
2. Timeline per application.
3. Follow-up email drafting (send/revise/skip pattern from onboarding).
4. Status change notifications on login.

---

## Summary of v2 Changes from v1

| Area | v1 Behavior | v2 Behavior |
|------|-------------|-------------|
| **Data saving** | Agent silently saves extracted data to DB immediately | Agent shows confirmation card → user reviews/edits → user clicks Save → then DB write |
| **Resume upload** | Save file, extract text, parse, merge — all in one shot | ALWAYS save raw PDF first (Step A) → attempt extraction with 3 fallback strategies (Step B) → if text found, LLM parse (Step C) → show confirmation card with parsed data (Step D) → user edits/confirms → then merge into profile |
| **Resume failure** | Not explicitly handled | Graceful degradation: raw file saved, agent tells user parsing failed, falls back to manual questions, original PDF still attached to all applications |
| **Profile editing** | Only via chat commands, agent-mediated | Three ways: (1) Full profile page with editable form cards accessible anytime via header, (2) Chat commands that show mini confirmation cards, (3) Inline editing in confirmation cards during profile building |
| **Skill editing** | Implicit from messages | Explicit add/remove/edit on profile page with proficiency dropdowns and years field |
| **Education editing** | Implicit from messages | Full CRUD on profile page: add entries with institution/degree/field/year, edit inline, delete with confirmation |
| **Experience editing** | Implicit from messages | Full CRUD on profile page: add entries with company/role/dates/description, edit inline, delete with confirmation |
| **Cover letter** | Generated and shown for review | Generated, shown in editable text area, user can directly modify text or ask for LLM revision |
| **Embedding updates** | Not specified when triggered | Re-computed on every profile change (save from confirmation card, manual edit, resume parse) |

### New API Endpoints (v2 additions)

```
POST  /api/applicant/profile/confirm        → Save data from confirmation card
GET   /api/applicant/profile                 → Full profile (all sections, editable)
PUT   /api/applicant/profile                 → Update basic profile fields
POST  /api/applicant/profile/skills          → Add a skill
PUT   /api/applicant/profile/skills/{id}     → Update a skill
DELETE /api/applicant/profile/skills/{id}    → Remove a skill
POST  /api/applicant/profile/education       → Add education entry
PUT   /api/applicant/profile/education/{id}  → Update education entry
DELETE /api/applicant/profile/education/{id} → Remove education entry
POST  /api/applicant/profile/experience      → Add experience entry
PUT   /api/applicant/profile/experience/{id} → Update experience entry
DELETE /api/applicant/profile/experience/{id}→ Remove experience entry
POST  /api/applicant/profile/resume/upload   → Upload new resume (raw save + parse attempt)
DELETE /api/applicant/profile/resume         → Remove resume
GET   /api/applicant/profile/resume/download → Download saved resume
```
