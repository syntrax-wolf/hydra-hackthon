# Hydra — Multi-Agent Business Intelligence & Applicant Management Platform

Hydra is an AI-powered enterprise platform that combines **business intelligence reporting** with an **intelligent applicant tracking system**. Managers can ask natural-language questions about finance, inventory, and HR — and receive professional PDF/PPTX/XLSX reports with charts, tables, and actionable recommendations. Job applicants get a conversational AI agent that guides them through profile building, skill gap analysis, job discovery, and application tracking.

---

## Features

### Business Intelligence Agent
- **Natural Language Queries** — Ask questions like *"Compare profit margins across all offices for Q4 2025"* and get structured reports
- **Multi-Domain Coverage** — Finance, Inventory, HR, and Employee Onboarding data
- **Professional Report Generation** — Auto-generates PDF, PPTX, and XLSX reports with charts, tables, and executive summaries
- **3-Agent Code Generation Pipeline** — Coder, Syntax Checker, and Code Reviewer agents collaborate to produce high-quality report scripts
- **Quality Scoring Loop** — Reports are scored on 4 dimensions (data completeness, analysis depth, visual quality, document quality) and iteratively improved until they meet a 70/100 threshold
- **Secure Data Access** — No raw LLM-generated SQL; all queries are parameterized with column/table whitelists

### Employee Onboarding Agent
- **Automated Onboarding Workflow** — Extract employee details, provision system accounts (email, Slack, GitHub, Jira), draft welcome emails, schedule kickoff meetings, and generate onboarding documents
- **Interactive Email Review** — Managers can approve, revise, or skip welcome emails before sending
- **Calendar Integration** — Finds available meeting slots on the manager's calendar

### Applicant Tracking Agent
- **Conversational Profile Building** — AI agent guides applicants through a 7-phase journey: onboarding, profile creation, skill gap analysis, job discovery, application, interview prep, and tracking
- **Resume Parsing & Embedding** — Uploads are parsed, embedded with sentence-transformers, and stored for semantic search
- **Skill Gap Analysis** — Identifies missing skills and recommends YouTube playlists for upskilling
- **Job Matching** — Matches applicant profiles to job openings using vector similarity via HydraDB
- **Application Dashboard** — Track application status, follow-ups, and interview prep materials

---

## Architecture

```
                          +-------------------+
       User Query ------->|   FastAPI Server   |
                          |   (service.py)     |
                          +--------+----------+
                                   |
                    +--------------+--------------+------------------+
                    |                             |                  |
             Finance Pipeline           Onboarding Pipeline   Applicant Pipeline
                    |                             |                  |
           +--------v---------+        +----------v---------+   +---v-----------+
           |  1. Decompose    |        | Extract Employee   |   | Resume Parse  |
           |  (Finance LLM)   |        | Provision Accounts |   | Profile Build |
           +--------+---------+        | Draft Email        |   | Job Matching  |
                    |                  | Schedule Meeting   |   | Interview Prep|
           +--------v---------+        | Generate Doc       |   +---------------+
           |  2. Fetch Data   |        +--------------------+
           |  (PostgreSQL)    |
           +--------+---------+
                    |
           +--------v---------+
           |  3. Analyze      |
           |  (Finance LLM)   |
           +--------+---------+
                    |
           +--------v---------+
           |  4. Code Gen     |
           |  3-Agent Pipeline|
           |  Coder -> Syntax |
           |  -> Reviewer     |
           +--------+---------+
                    |
           +--------v---------+
           |  5. Sandbox Exec |
           |  (subprocess)    |
           +------------------+
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **LLM** | OpenRouter API (Qwen 3.5-27B / Qwen3-Coder-Next) |
| **Backend** | FastAPI + Uvicorn (async) |
| **Database** | PostgreSQL 16 + pgvector |
| **Vector Store** | HydraDB (for applicant job matching) |
| **Embeddings** | sentence-transformers |
| **Report Gen** | ReportLab (PDF), python-pptx (PPTX), openpyxl (XLSX) |
| **Charts** | Matplotlib |
| **Frontend** | Vanilla HTML/CSS/JS (no build step) |
| **Containerization** | Docker + Docker Compose |

---

## Database Schema

PostgreSQL with **5 schemas** covering multiple business domains:

### Inventory (6 tables)
`products` (75 rows), `warehouses` (7), `inventory_levels` (525), `stock_movements` (5,000), `product_pricing` (75), `price_history` (1,497)

### HR (4 tables)
`employees` (100), `employee_skills` (526), `performance_reviews` (200), `leave_records` (349)

### Finance (2 tables + 2 materialized views)
`offices` (7), `sales_transactions` (10,000), `mv_daily_office_profit_loss` (1,259), `mv_daily_product_revenue` (5,520)

### Onboarding (4 tables)
`onboarding_records`, `manager_schedule`, `email_drafts`, `system_accounts`

### Applicant (6 tables)
`applicant_profiles`, `applicant_skills`, `applicant_experience`, `applicant_education`, `job_listings`, `applications`

**Key dimensions:** 7 cities (Mumbai, Delhi, Bangalore, etc.) | 5 product categories | 8 departments | Date range: Sep 2025 - Feb 2026

---

## Getting Started

### Prerequisites

- **OpenRouter API key** — [openrouter.ai](https://openrouter.ai)
- **Docker & Docker Compose** (recommended), OR **Python 3.12+** and **PostgreSQL 16**

### Option A: Docker (Recommended)

```bash
# 1. Clone the repo
git clone https://github.com/syntrax-wolf/hydra-hackthon.git
cd hydra-hackthon

# 2. Create .env from template
cp .env.example .env
# Edit .env — add your OPENROUTER_API_KEY and set POSTGRES_PASSWORD

# 3. Start the stack
docker compose up -d

# 4. Wait for seeding (~2-3 min on first run)
docker compose logs -f db-seed

# 5. Open the app
open http://localhost:8501
```

Docker Compose starts 3 services:
| Service | Description |
|---------|-------------|
| `db` | PostgreSQL 16 with pgvector — runs SQL schema files on first boot |
| `db-seed` | One-shot container that populates all tables with synthetic data |
| `app` | FastAPI server on port 8501 |

### Option B: Local Setup

```bash
# 1. Set up PostgreSQL and run schema files
psql -U postgres -d postgres -f hydra_agent/00_extensions.sql
psql -U postgres -d postgres -f hydra_agent/01_inventory.sql
psql -U postgres -d postgres -f hydra_agent/02_hr.sql
psql -U postgres -d postgres -f hydra_agent/03_finance.sql
psql -U postgres -d postgres -f hydra_agent/04_applicant.sql

# 2. Seed the data
pip install asyncpg numpy faker psycopg2-binary sentence-transformers
python hydra_agent/seed_data.py
python hydra_agent/seed_applicant_data.py

# 3. Configure environment
cp .env.example .env
# Edit .env with your credentials

# 4. Install dependencies and validate
python setup.py

# 5. Start the server
python service.py
# In another terminal:
python main.py
```

---

## Usage

### Business Intelligence Queries

Type natural-language questions in the chat interface:

**Finance:**
- *"Compare profit margins across all offices for Q4 2025"*
- *"Which office had the highest revenue last month?"*

**Inventory:**
- *"What are the top 10 products by stock movement volume?"*
- *"Which warehouses are running low on electronics?"*

**HR:**
- *"Compare average performance ratings across departments"*
- *"Show leave utilization by department this quarter"*

The agent returns a **narrative analysis** with executive summary, key findings, and recommendations, plus a **downloadable report** (PDF/PPTX/XLSX) with charts and tables.

### Employee Onboarding

- *"Start onboarding for Rahul Sharma in engineering"*
- Pipeline: extract details -> find matching records -> provision accounts -> draft email -> schedule meeting -> generate onboarding doc

### Applicant Portal

Navigate to the applicant portal for the conversational AI agent that guides through:
1. Profile building (2-3 questions at a time)
2. Resume upload and parsing
3. Skill gap analysis with YouTube learning recommendations
4. Job matching and application submission
5. Interview preparation with mock questions
6. Application tracking dashboard

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Landing page |
| `/api/query` | POST | Main query endpoint (routes to finance or onboarding) |
| `/api/onboarding/dashboard` | GET | All onboarding records with summary stats |
| `/api/onboarding/select-employee` | POST | Disambiguate employee matches |
| `/api/onboarding/{id}/email-action` | POST | Approve/revise/send/skip welcome email |
| `/api/onboarding/{id}/select-slot` | POST | Book a kickoff meeting slot |
| `/api/download/{filename}` | GET | Download a generated report |

---

## Project Structure

```
hydra-hackthon/
├── service.py                  # FastAPI app with all endpoints
├── main.py                     # Opens browser (server must be running)
├── setup.py                    # DB validation + onboarding table setup
├── requirements.txt            # Python dependencies
├── Dockerfile                  # App container image
├── docker-compose.yml          # Full stack: db + seed + app
├── .env.example                # Environment variable template
├── .gitignore
├── LICENSE
│
├── core/
│   ├── config.py               # Loads .env into Config dataclass
│   ├── db.py                   # Database layer (whitelist, query builder, pool)
│   ├── orchestrator.py         # 5-step BI pipeline with quality loop
│   ├── onboarding_orchestrator.py  # Multi-step onboarding workflow
│   ├── applicant_orchestrator.py   # Applicant agent workflow
│   ├── sandbox.py              # Subprocess execution with timeout
│   └── schemas.py              # Pydantic request/response models
│
├── agents/
│   ├── finance_agent.py        # Decompose + Analyze LLM calls
│   ├── coding_agent.py         # 3-agent code gen pipeline
│   ├── syntax_checker.py       # LLM-based syntax review
│   ├── code_reviewer.py        # LLM-based execution review
│   ├── onboarding_agent.py     # Onboarding-specific LLM calls
│   ├── applicant_agent.py      # Applicant portal LLM calls
│   └── prompts.py              # All system prompts + few-shot examples
│
├── applicant/
│   ├── resume_processor.py     # Resume parsing and storage
│   ├── embeddings.py           # Sentence-transformer embeddings
│   ├── job_matcher.py          # Vector similarity job matching
│   ├── profile_manager.py      # Applicant profile CRUD
│   ├── application_manager.py  # Application tracking
│   ├── youtube_search.py       # YouTube API for learning resources
│   └── hydra_retriever.py      # HydraDB vector store integration
│
├── onboarding/
│   ├── provisioner.py          # System account provisioning
│   ├── calendar_scheduler.py   # Manager calendar availability
│   ├── email_composer.py       # Welcome email drafting
│   └── doc_generator.py        # Onboarding document generation
│
├── ui/
│   ├── landing.html            # Landing/home page
│   ├── index.html              # BI agent chat interface
│   └── applicant.html          # Applicant portal interface
│
├── docker/
│   └── entrypoint.sh           # App container entrypoint
│
├── hydra_agent/
│   ├── 00_extensions.sql       # pgvector + schema creation
│   ├── 01_inventory.sql        # Inventory tables + indexes
│   ├── 02_hr.sql               # HR tables + indexes
│   ├── 03_finance.sql          # Finance tables + materialized views
│   ├── 04_applicant.sql        # Applicant tables
│   ├── seed_data.py            # Synthetic data generator
│   └── seed_applicant_data.py  # Applicant data seeder
│
├── generated/                  # Runtime output (reports, charts)
│
└── docs/
    └── APPLICANT_AGENT_WORKFLOW_v2.md  # Detailed applicant agent spec
```

---

## Configuration

All configuration is via environment variables (`.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | *(required)* | Your OpenRouter API key |
| `OPENROUTER_MODEL` | `qwen/qwen3.5-27b` | Model for decomposition and analysis |
| `OPENROUTER_CODING_MODEL` | `qwen/qwen3-coder-next` | Model for code generation |
| `POSTGRES_HOST` | `localhost` / `db` (Docker) | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_USER` | `postgres` | PostgreSQL user |
| `POSTGRES_PASSWORD` | *(required)* | PostgreSQL password |
| `POSTGRES_DB` | `horizon` | PostgreSQL database name |
| `SERVER_PORT` | `8501` | Server port |
| `SANDBOX_TIMEOUT` | `60` | Max seconds for code execution |
| `YOUTUBE_API_KEY` | *(optional)* | YouTube Data API key for learning resources |
| `HYDRA_API_KEY` | *(optional)* | HydraDB API key for vector job matching |
| `HYDRA_TENANT_ID` | `hydra_agent` | HydraDB tenant identifier |

---

## Security

- **No raw LLM SQL** — LLM outputs structured JSON plans; all queries are built server-side with parameterized `psycopg2.sql` identifiers
- **Table/Column whitelists** — Only pre-approved tables and columns can be queried
- **Aggregate whitelist** — Only `SUM`, `AVG`, `COUNT`, `MIN`, `MAX` allowed
- **Sandboxed execution** — Generated Python scripts run in a subprocess with configurable timeout
- **Path traversal prevention** — Download endpoint rejects filenames containing `/`, `\`, or `..`
- **Connection pooling** — `psycopg2.pool.ThreadedConnectionPool` with 1-10 connections

---

## License

MIT
