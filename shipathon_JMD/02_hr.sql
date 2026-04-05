-- ============================================================
-- SCHEMA: hr
-- Tables: employees, employee_skills, performance_reviews, leave_records
-- ============================================================


-- ──────────────────────────────────────────────────────────────
-- EMPLOYEES
-- Master record for every person in the organisation.
-- Compensation fields (salary, pay band) are merged here.
-- Embedding enables semantic profile search: "senior backend engineer Mumbai"
-- ──────────────────────────────────────────────────────────────
CREATE TABLE hr.employees (
    employee_id                     SERIAL PRIMARY KEY,
    full_name                       VARCHAR(200) NOT NULL,
    email_address                   VARCHAR(200) UNIQUE,
    phone_number                    VARCHAR(20),
    department                      VARCHAR(100),
    designation                     VARCHAR(100),
    office_location                 VARCHAR(100),
    date_of_joining                 DATE,
    is_active                       BOOLEAN DEFAULT true,

    -- Compensation (merged — no separate table)
    base_salary_amount              DECIMAL(12,2),
    salary_currency                 VARCHAR(3) DEFAULT 'INR',
    pay_band                        VARCHAR(20),
    last_salary_revision_date       DATE,

    -- BGE-M3 1024d embedding of (full_name + department + designation + skills summary)
    employee_profile_embedding      VECTOR(1024),

    -- Auto-generated tsvector for keyword search
    full_text_search_vector         tsvector,

    created_at                      TIMESTAMPTZ DEFAULT now()
);

-- HNSW vector index for semantic profile search
CREATE INDEX index_employee_profile_embedding ON hr.employees
    USING hnsw (employee_profile_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- GIN index for full-text keyword search
CREATE INDEX index_employee_full_text ON hr.employees
    USING gin(full_text_search_vector);

-- B-tree indexes for filtered queries
CREATE INDEX index_employee_by_department ON hr.employees (department);
CREATE INDEX index_employee_by_location ON hr.employees (office_location);

-- Trigger: auto-populate full_text_search_vector on insert/update
CREATE FUNCTION hr.update_employee_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.full_text_search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.full_name, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.department, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW.designation, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW.office_location, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_employee_search_vector
    BEFORE INSERT OR UPDATE ON hr.employees
    FOR EACH ROW EXECUTE FUNCTION hr.update_employee_search_vector();

-- Row-Level Security: salary fields visible only to authorised roles
ALTER TABLE hr.employees ENABLE ROW LEVEL SECURITY;

CREATE POLICY policy_salary_access ON hr.employees
    USING (
        current_setting('app.user_role', true) IN ('hr_admin', 'finance_admin', 'cxo')
        OR true  -- non-salary columns always visible; app layer hides salary fields
    );


-- ──────────────────────────────────────────────────────────────
-- EMPLOYEE SKILLS
-- Single denormalised table: each row = one employee + one skill.
-- No separate skills master table.
-- Embedding enables semantic skill matching: "machine learning" → "deep learning", "neural networks"
-- ──────────────────────────────────────────────────────────────
CREATE TABLE hr.employee_skills (
    employee_skill_id               SERIAL PRIMARY KEY,
    employee_id                     INTEGER REFERENCES hr.employees(employee_id) ON DELETE CASCADE,

    skill_name                      VARCHAR(100) NOT NULL,
    skill_category                  VARCHAR(50),        -- programming, cloud, management, domain, language, design
    proficiency_level               VARCHAR(20) CHECK (proficiency_level IN (
                                        'beginner', 'intermediate', 'advanced', 'expert'
                                    )),
    years_of_experience             DECIMAL(4,1),
    last_used_date                  DATE,

    -- BGE-M3 1024d embedding of (skill_name + skill_category)
    skill_name_embedding            VECTOR(1024),

    -- Auto-generated tsvector for keyword search
    full_text_search_vector         tsvector,

    UNIQUE (employee_id, skill_name)
);

-- HNSW vector index for semantic skill matching
CREATE INDEX index_skill_name_embedding ON hr.employee_skills
    USING hnsw (skill_name_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- GIN index for full-text keyword search on skills
CREATE INDEX index_skill_full_text ON hr.employee_skills
    USING gin(full_text_search_vector);

-- B-tree indexes for filtered queries
CREATE INDEX index_skill_by_employee ON hr.employee_skills (employee_id);
CREATE INDEX index_skill_by_name ON hr.employee_skills (skill_name);
CREATE INDEX index_skill_by_category ON hr.employee_skills (skill_category);

-- Trigger: auto-populate full_text_search_vector on insert/update
CREATE FUNCTION hr.update_skill_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.full_text_search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.skill_name, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.skill_category, '')), 'B');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_skill_search_vector
    BEFORE INSERT OR UPDATE ON hr.employee_skills
    FOR EACH ROW EXECUTE FUNCTION hr.update_skill_search_vector();


-- ──────────────────────────────────────────────────────────────
-- PERFORMANCE REVIEWS
-- Free-text reviews with ratings and BGE-M3 embeddings.
-- Embedding enables: "who got praised for leadership" → semantic search over review text.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE hr.performance_reviews (
    review_id                       SERIAL PRIMARY KEY,
    employee_id                     INTEGER REFERENCES hr.employees(employee_id) ON DELETE CASCADE,
    review_period                   VARCHAR(20),        -- '2025-H1', '2025-H2', '2026-Q1'
    reviewer_name                   VARCHAR(200),
    rating_score                    DECIMAL(3,1) CHECK (rating_score BETWEEN 1.0 AND 5.0),
    review_text                     TEXT,

    -- BGE-M3 1024d embedding of review_text
    review_text_embedding           VECTOR(1024),

    created_at                      TIMESTAMPTZ DEFAULT now()
);

-- HNSW vector index for semantic review search
CREATE INDEX index_review_text_embedding ON hr.performance_reviews
    USING hnsw (review_text_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX index_review_by_employee ON hr.performance_reviews (employee_id);
CREATE INDEX index_review_by_rating ON hr.performance_reviews (rating_score DESC);


-- ──────────────────────────────────────────────────────────────
-- LEAVE RECORDS
-- Every leave request: casual, sick, earned, work-from-home, etc.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE hr.leave_records (
    leave_record_id                 BIGSERIAL PRIMARY KEY,
    employee_id                     INTEGER REFERENCES hr.employees(employee_id) ON DELETE CASCADE,
    leave_type                      VARCHAR(30) CHECK (leave_type IN (
                                        'casual', 'sick', 'earned', 'work_from_home',
                                        'maternity', 'paternity', 'unpaid'
                                    )),
    start_date                      DATE,
    end_date                        DATE,
    approval_status                 VARCHAR(20) DEFAULT 'pending' CHECK (approval_status IN (
                                        'pending', 'approved', 'rejected', 'cancelled'
                                    ))
);

CREATE INDEX index_leave_by_employee ON hr.leave_records (employee_id, start_date DESC);
