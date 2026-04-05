-- ============================================================
-- 04_applicant.sql — Applicant schema for the Horizon platform
-- Creates: applicant schema with 9 tables + indexes + triggers
-- ============================================================

CREATE SCHEMA IF NOT EXISTS applicant;

-- ── APPLICANT PROFILES ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS applicant.applicant_profiles (
    applicant_id          SERIAL PRIMARY KEY,
    full_name             VARCHAR(200),
    email                 VARCHAR(200) UNIQUE NOT NULL,
    phone                 VARCHAR(30),
    headline              TEXT,
    summary               TEXT,
    desired_role          TEXT,
    desired_department    VARCHAR(100),
    experience_years      INTEGER,
    current_company       VARCHAR(200),
    "current_role"        VARCHAR(200),
    location_preference   JSONB DEFAULT '[]'::jsonb,
    willing_to_relocate   BOOLEAN,
    salary_min            NUMERIC(12,2),
    salary_max            NUMERIC(12,2),
    salary_currency       VARCHAR(10) DEFAULT 'INR',
    job_type_preference   JSONB DEFAULT '[]'::jsonb,
    linkedin_url          VARCHAR(500),
    github_url            VARCHAR(500),
    portfolio_url         VARCHAR(500),
    resume_file_path      VARCHAR(500),
    resume_updated_at     TIMESTAMPTZ,
    profile_embedding     VECTOR(1024),
    onboarding_phase      INTEGER NOT NULL DEFAULT 1,
    profile_completion    INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ DEFAULT now(),
    updated_at            TIMESTAMPTZ DEFAULT now(),
    full_text_search_vector tsvector
);

CREATE INDEX IF NOT EXISTS idx_applicant_profiles_email ON applicant.applicant_profiles(email);
CREATE INDEX IF NOT EXISTS idx_applicant_profiles_phase ON applicant.applicant_profiles(onboarding_phase);
CREATE INDEX IF NOT EXISTS idx_applicant_profiles_embedding ON applicant.applicant_profiles
    USING hnsw (profile_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_applicant_profiles_fts ON applicant.applicant_profiles
    USING gin(full_text_search_vector);

-- Auto-update tsvector on profile changes
CREATE OR REPLACE FUNCTION applicant.update_profile_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.full_text_search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.desired_role, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.headline, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.summary, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW."current_role", '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_profile_search_vector ON applicant.applicant_profiles;
CREATE TRIGGER trigger_profile_search_vector
    BEFORE INSERT OR UPDATE ON applicant.applicant_profiles
    FOR EACH ROW EXECUTE FUNCTION applicant.update_profile_search_vector();


-- ── SKILLS ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS applicant.skills (
    skill_id              SERIAL PRIMARY KEY,
    applicant_id          INTEGER NOT NULL REFERENCES applicant.applicant_profiles(applicant_id) ON DELETE CASCADE,
    skill_name            VARCHAR(200) NOT NULL,
    proficiency_level     VARCHAR(30) DEFAULT 'intermediate'
                          CHECK (proficiency_level IN ('beginner', 'intermediate', 'advanced', 'expert')),
    years_of_experience   INTEGER DEFAULT 0,
    created_at            TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skills_applicant ON applicant.skills(applicant_id);
CREATE INDEX IF NOT EXISTS idx_skills_name ON applicant.skills(skill_name);


-- ── EDUCATION ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS applicant.education (
    education_id          SERIAL PRIMARY KEY,
    applicant_id          INTEGER NOT NULL REFERENCES applicant.applicant_profiles(applicant_id) ON DELETE CASCADE,
    institution           VARCHAR(300) NOT NULL,
    degree                VARCHAR(200),
    field_of_study        VARCHAR(200),
    start_year            INTEGER,
    end_year              INTEGER,
    gpa_grade             VARCHAR(20),
    created_at            TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_education_applicant ON applicant.education(applicant_id);


-- ── EXPERIENCE ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS applicant.experience (
    experience_id         SERIAL PRIMARY KEY,
    applicant_id          INTEGER NOT NULL REFERENCES applicant.applicant_profiles(applicant_id) ON DELETE CASCADE,
    company_name          VARCHAR(300) NOT NULL,
    role_title            VARCHAR(200) NOT NULL,
    start_date            DATE,
    end_date              DATE,
    is_current            BOOLEAN DEFAULT false,
    description           TEXT,
    created_at            TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_experience_applicant ON applicant.experience(applicant_id);


-- ── JOB POSTINGS ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS applicant.job_postings (
    job_id                SERIAL PRIMARY KEY,
    title                 VARCHAR(300) NOT NULL,
    company               VARCHAR(300) NOT NULL,
    department            VARCHAR(100),
    description           TEXT,
    required_skills       JSONB DEFAULT '[]'::jsonb,
    preferred_skills      JSONB DEFAULT '[]'::jsonb,
    experience_min        INTEGER,
    experience_max        INTEGER,
    salary_min            NUMERIC(12,2),
    salary_max            NUMERIC(12,2),
    salary_currency       VARCHAR(10) DEFAULT 'INR',
    location              JSONB DEFAULT '[]'::jsonb,
    job_type              VARCHAR(30) DEFAULT 'full_time'
                          CHECK (job_type IN ('full_time', 'part_time', 'contract', 'internship', 'freelance')),
    status                VARCHAR(30) DEFAULT 'open'
                          CHECK (status IN ('open', 'closed', 'filled', 'draft')),
    posted_at             TIMESTAMPTZ DEFAULT now(),
    deadline              DATE,
    posting_embedding     VECTOR(1024),
    full_text_search_vector tsvector
);

CREATE INDEX IF NOT EXISTS idx_job_postings_status ON applicant.job_postings(status);
CREATE INDEX IF NOT EXISTS idx_job_postings_embedding ON applicant.job_postings
    USING hnsw (posting_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_job_postings_fts ON applicant.job_postings
    USING gin(full_text_search_vector);

-- Auto-update tsvector on job posting changes
CREATE OR REPLACE FUNCTION applicant.update_job_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.full_text_search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.company, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_job_search_vector ON applicant.job_postings;
CREATE TRIGGER trigger_job_search_vector
    BEFORE INSERT OR UPDATE ON applicant.job_postings
    FOR EACH ROW EXECUTE FUNCTION applicant.update_job_search_vector();


-- ── APPLICATIONS ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS applicant.applications (
    application_id        SERIAL PRIMARY KEY,
    applicant_id          INTEGER NOT NULL REFERENCES applicant.applicant_profiles(applicant_id),
    job_id                INTEGER NOT NULL REFERENCES applicant.job_postings(job_id),
    status                VARCHAR(30) DEFAULT 'submitted'
                          CHECK (status IN ('draft', 'submitted', 'under_review', 'interview', 'offered', 'rejected', 'withdrawn', 'accepted')),
    cover_letter          TEXT,
    resume_snapshot_path  VARCHAR(500),
    match_score           INTEGER,
    applied_at            TIMESTAMPTZ DEFAULT now(),
    updated_at            TIMESTAMPTZ DEFAULT now(),
    UNIQUE(applicant_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_applications_applicant ON applicant.applications(applicant_id);
CREATE INDEX IF NOT EXISTS idx_applications_job ON applicant.applications(job_id);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applicant.applications(status);


-- ── APPLICATION TIMELINE ────────────────────────────────────
CREATE TABLE IF NOT EXISTS applicant.application_timeline (
    event_id              SERIAL PRIMARY KEY,
    application_id        INTEGER NOT NULL REFERENCES applicant.applications(application_id) ON DELETE CASCADE,
    event_type            VARCHAR(50) NOT NULL,
    details               TEXT,
    created_at            TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_timeline_application ON applicant.application_timeline(application_id);


-- ── SAVED JOBS ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS applicant.saved_jobs (
    saved_id              SERIAL PRIMARY KEY,
    applicant_id          INTEGER NOT NULL REFERENCES applicant.applicant_profiles(applicant_id) ON DELETE CASCADE,
    job_id                INTEGER NOT NULL REFERENCES applicant.job_postings(job_id),
    saved_at              TIMESTAMPTZ DEFAULT now(),
    UNIQUE(applicant_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_saved_jobs_applicant ON applicant.saved_jobs(applicant_id);


-- ── INTERVIEW PREP ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS applicant.interview_prep (
    prep_id               SERIAL PRIMARY KEY,
    application_id        INTEGER NOT NULL REFERENCES applicant.applications(application_id),
    content               JSONB NOT NULL DEFAULT '{}'::jsonb,
    cached_at             TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_interview_prep_application ON applicant.interview_prep(application_id);
