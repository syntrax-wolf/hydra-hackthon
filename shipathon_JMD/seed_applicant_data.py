"""Seed applicant data — job postings + sample applicant profiles.

Run after 04_applicant.sql has been applied.
Uses sentence-transformers BGE-M3 for real embeddings.
"""

import os
import sys
import json
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("seed_applicant")

# ── Connection ───────────────────────────────────────────────

DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
DB_USER = os.environ.get("POSTGRES_USER", "postgres")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "")
DB_NAME = os.environ.get("POSTGRES_DB", "horizon")


def get_conn():
    import psycopg2
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                            password=DB_PASS, database=DB_NAME)


# ── Embedding helper ─────────────────────────────────────────

_model = None

def compute_embedding(text: str) -> list[float]:
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("BAAI/bge-m3")
            log.info("BGE-M3 model loaded")
        except ImportError:
            log.warning("sentence-transformers not available, using random embeddings")
            import numpy as np
            def _random(t):
                v = np.random.randn(1024).astype(float)
                v /= np.linalg.norm(v)
                return v.tolist()
            _model = type("Mock", (), {"encode": lambda self, t, **kw: _random(t)})()

    result = _model.encode(text, normalize_embeddings=True)
    if hasattr(result, "tolist"):
        return result.tolist()
    return list(result)


# ── Job Postings ─────────────────────────────────────────────

JOB_POSTINGS = [
    {
        "title": "Senior Backend Engineer",
        "company": "Horizon Technologies",
        "department": "engineering",
        "description": "Design and build scalable microservices using Python and PostgreSQL. Lead technical architecture decisions for our data pipeline infrastructure. Mentor junior engineers and conduct code reviews.",
        "required_skills": ["Python", "PostgreSQL", "Docker", "REST APIs", "Microservices"],
        "preferred_skills": ["Kubernetes", "AWS", "Redis", "GraphQL", "CI/CD"],
        "experience_min": 3, "experience_max": 7,
        "salary_min": 1800000, "salary_max": 3000000,
        "location": ["Bangalore", "Remote"], "job_type": "full_time",
    },
    {
        "title": "Full Stack Developer",
        "company": "Horizon Technologies",
        "department": "engineering",
        "description": "Build end-to-end features across our React frontend and FastAPI backend. Work closely with design and product teams to deliver user-facing features.",
        "required_skills": ["JavaScript", "React", "Python", "FastAPI", "SQL"],
        "preferred_skills": ["TypeScript", "Next.js", "Docker", "Tailwind CSS", "Git"],
        "experience_min": 2, "experience_max": 5,
        "salary_min": 1200000, "salary_max": 2200000,
        "location": ["Bangalore", "Mumbai"], "job_type": "full_time",
    },
    {
        "title": "Data Scientist",
        "company": "Horizon Technologies",
        "department": "data_science",
        "description": "Develop ML models for demand forecasting and pricing optimization. Work with large datasets, build data pipelines, and deploy models to production.",
        "required_skills": ["Python", "Machine Learning", "SQL", "Pandas", "Statistics"],
        "preferred_skills": ["TensorFlow", "PyTorch", "Spark", "Deep Learning", "A/B Testing"],
        "experience_min": 2, "experience_max": 6,
        "salary_min": 1500000, "salary_max": 2800000,
        "location": ["Bangalore", "Delhi", "Remote"], "job_type": "full_time",
    },
    {
        "title": "ML Engineer",
        "company": "Horizon Technologies",
        "department": "data_science",
        "description": "Build and optimize ML inference pipelines. Deploy models at scale using Kubernetes and cloud services. Monitor model performance and implement retraining strategies.",
        "required_skills": ["Python", "TensorFlow", "Docker", "Kubernetes", "SQL"],
        "preferred_skills": ["AWS SageMaker", "MLflow", "Spark", "Go", "Redis"],
        "experience_min": 3, "experience_max": 7,
        "salary_min": 2000000, "salary_max": 3500000,
        "location": ["Bangalore"], "job_type": "full_time",
    },
    {
        "title": "UI/UX Designer",
        "company": "Horizon Technologies",
        "department": "design",
        "description": "Create beautiful, user-friendly interfaces for our enterprise SaaS products. Conduct user research, build prototypes, and collaborate with engineers on implementation.",
        "required_skills": ["Figma", "UI Design", "UX Research", "Prototyping", "Design Systems"],
        "preferred_skills": ["Adobe XD", "Sketch", "Interaction Design", "Accessibility", "Motion Design"],
        "experience_min": 2, "experience_max": 5,
        "salary_min": 1000000, "salary_max": 2000000,
        "location": ["Mumbai", "Bangalore"], "job_type": "full_time",
    },
    {
        "title": "DevOps Engineer",
        "company": "Horizon Technologies",
        "department": "engineering",
        "description": "Manage cloud infrastructure on AWS. Build CI/CD pipelines, monitor system health, and ensure 99.9% uptime for our production services.",
        "required_skills": ["AWS", "Docker", "Kubernetes", "CI/CD", "Linux"],
        "preferred_skills": ["Terraform", "Ansible", "Prometheus", "Grafana", "Python"],
        "experience_min": 3, "experience_max": 6,
        "salary_min": 1800000, "salary_max": 3200000,
        "location": ["Bangalore", "Hyderabad"], "job_type": "full_time",
    },
    {
        "title": "Product Manager",
        "company": "Horizon Technologies",
        "department": "product",
        "description": "Own the product roadmap for our inventory management module. Define requirements, prioritize features, and work with engineering and design teams to deliver value.",
        "required_skills": ["Product Strategy", "Roadmapping", "SQL", "User Research", "Agile"],
        "preferred_skills": ["Data Analysis", "Jira", "Figma", "A/B Testing", "Stakeholder Management"],
        "experience_min": 4, "experience_max": 8,
        "salary_min": 2000000, "salary_max": 3500000,
        "location": ["Mumbai", "Bangalore"], "job_type": "full_time",
    },
    {
        "title": "Digital Marketing Manager",
        "company": "Horizon Technologies",
        "department": "marketing",
        "description": "Lead digital marketing campaigns across SEO, SEM, and social media. Analyze campaign performance and optimize for conversion.",
        "required_skills": ["SEO", "Google Analytics", "Content Strategy", "Social Media", "SEM"],
        "preferred_skills": ["HubSpot", "Email Marketing", "Paid Ads", "Copywriting", "CRM"],
        "experience_min": 3, "experience_max": 6,
        "salary_min": 1200000, "salary_max": 2200000,
        "location": ["Mumbai", "Delhi"], "job_type": "full_time",
    },
    {
        "title": "Sales Executive",
        "company": "Horizon Technologies",
        "department": "sales",
        "description": "Drive B2B sales for our enterprise products. Build and manage client relationships, negotiate contracts, and meet quarterly targets.",
        "required_skills": ["Lead Generation", "Negotiation", "CRM", "Presentation Skills", "Account Management"],
        "preferred_skills": ["Salesforce", "Cold Calling", "Revenue Forecasting", "Pipeline Management"],
        "experience_min": 2, "experience_max": 5,
        "salary_min": 800000, "salary_max": 1800000,
        "location": ["Mumbai", "Delhi", "Bangalore", "Chennai", "Pune", "Hyderabad", "Kolkata"],
        "job_type": "full_time",
    },
    {
        "title": "HR Business Partner",
        "company": "Horizon Technologies",
        "department": "hr_admin",
        "description": "Partner with business leaders to align HR strategies. Handle recruitment, performance management, and employee engagement initiatives.",
        "required_skills": ["Recruitment", "Performance Management", "Employee Engagement", "HRIS", "Labour Law"],
        "preferred_skills": ["Training", "Payroll", "Compliance", "Conflict Resolution"],
        "experience_min": 3, "experience_max": 7,
        "salary_min": 1000000, "salary_max": 2000000,
        "location": ["Mumbai", "Delhi"], "job_type": "full_time",
    },
    {
        "title": "Financial Analyst",
        "company": "Horizon Technologies",
        "department": "finance_ops",
        "description": "Analyze financial data, build models, and prepare reports for leadership. Support budgeting, forecasting, and strategic planning.",
        "required_skills": ["Financial Modelling", "Excel", "Accounting", "Budgeting", "Forecasting"],
        "preferred_skills": ["SAP", "Tally", "GST", "Audit", "Tax Planning"],
        "experience_min": 2, "experience_max": 5,
        "salary_min": 900000, "salary_max": 1800000,
        "location": ["Mumbai"], "job_type": "full_time",
    },
    {
        "title": "Frontend Developer",
        "company": "Horizon Technologies",
        "department": "engineering",
        "description": "Build responsive, performant web applications using React and TypeScript. Implement design systems and ensure cross-browser compatibility.",
        "required_skills": ["React", "TypeScript", "HTML", "CSS", "JavaScript"],
        "preferred_skills": ["Next.js", "Tailwind CSS", "Jest", "Webpack", "GraphQL"],
        "experience_min": 1, "experience_max": 4,
        "salary_min": 800000, "salary_max": 1800000,
        "location": ["Bangalore", "Remote"], "job_type": "full_time",
    },
    {
        "title": "Data Engineering Intern",
        "company": "Horizon Technologies",
        "department": "data_science",
        "description": "Assist the data team in building ETL pipelines and data warehousing. Great opportunity to learn from experienced engineers.",
        "required_skills": ["Python", "SQL", "Git"],
        "preferred_skills": ["Pandas", "Spark", "Airflow", "Docker"],
        "experience_min": 0, "experience_max": 1,
        "salary_min": 300000, "salary_max": 600000,
        "location": ["Bangalore", "Delhi"], "job_type": "internship",
    },
    {
        "title": "QA Engineer",
        "company": "Horizon Technologies",
        "department": "engineering",
        "description": "Design and execute test plans. Build automated test suites for our web and API products. Report and track bugs through resolution.",
        "required_skills": ["Testing", "Selenium", "Python", "API Testing", "Bug Tracking"],
        "preferred_skills": ["Cypress", "Jest", "CI/CD", "Performance Testing", "Jira"],
        "experience_min": 1, "experience_max": 4,
        "salary_min": 700000, "salary_max": 1500000,
        "location": ["Hyderabad", "Bangalore"], "job_type": "full_time",
    },
    {
        "title": "Content Writer",
        "company": "Horizon Technologies",
        "department": "marketing",
        "description": "Create engaging content for blog posts, social media, and marketing collateral. Research industry trends and write SEO-optimized articles.",
        "required_skills": ["Copywriting", "SEO", "Content Strategy", "Research"],
        "preferred_skills": ["Social Media", "WordPress", "Email Marketing", "Analytics"],
        "experience_min": 1, "experience_max": 3,
        "salary_min": 500000, "salary_max": 1000000,
        "location": ["Mumbai", "Remote"], "job_type": "full_time",
    },
    {
        "title": "Cloud Solutions Architect",
        "company": "Horizon Technologies",
        "department": "engineering",
        "description": "Design cloud-native architectures on AWS/GCP. Lead migration projects and ensure security, scalability, and cost optimization.",
        "required_skills": ["AWS", "GCP", "Kubernetes", "Terraform", "System Design"],
        "preferred_skills": ["Azure", "Microservices", "Security", "Cost Optimization", "Python"],
        "experience_min": 5, "experience_max": 10,
        "salary_min": 3000000, "salary_max": 5000000,
        "location": ["Bangalore"], "job_type": "full_time",
    },
    {
        "title": "Mobile Developer (React Native)",
        "company": "Horizon Technologies",
        "department": "engineering",
        "description": "Build cross-platform mobile apps using React Native. Work on our customer-facing mobile application.",
        "required_skills": ["React Native", "JavaScript", "TypeScript", "REST APIs", "Git"],
        "preferred_skills": ["iOS", "Android", "Redux", "Firebase", "CI/CD"],
        "experience_min": 2, "experience_max": 5,
        "salary_min": 1200000, "salary_max": 2500000,
        "location": ["Bangalore", "Pune"], "job_type": "full_time",
    },
    {
        "title": "Business Analyst",
        "company": "Horizon Technologies",
        "department": "product",
        "description": "Bridge business requirements and technical solutions. Analyze processes, document requirements, and support product launches.",
        "required_skills": ["Data Analysis", "SQL", "Excel", "Requirements Gathering", "Stakeholder Management"],
        "preferred_skills": ["Jira", "Tableau", "Python", "Agile", "Process Mapping"],
        "experience_min": 1, "experience_max": 4,
        "salary_min": 800000, "salary_max": 1600000,
        "location": ["Mumbai", "Delhi", "Bangalore"], "job_type": "full_time",
    },
    {
        "title": "Security Engineer",
        "company": "Horizon Technologies",
        "department": "engineering",
        "description": "Conduct security assessments, implement security controls, and respond to incidents. Ensure compliance with security standards.",
        "required_skills": ["Network Security", "Penetration Testing", "OWASP", "Linux", "Python"],
        "preferred_skills": ["AWS Security", "SIEM", "Incident Response", "Compliance", "Docker"],
        "experience_min": 3, "experience_max": 7,
        "salary_min": 2000000, "salary_max": 3500000,
        "location": ["Bangalore", "Hyderabad"], "job_type": "full_time",
    },
    {
        "title": "Technical Writer",
        "company": "Horizon Technologies",
        "department": "product",
        "description": "Write clear technical documentation for APIs, SDKs, and developer guides. Maintain docs-as-code practices.",
        "required_skills": ["Technical Writing", "API Documentation", "Markdown", "Git"],
        "preferred_skills": ["Python", "OpenAPI", "Swagger", "Confluence", "Docusaurus"],
        "experience_min": 1, "experience_max": 4,
        "salary_min": 600000, "salary_max": 1400000,
        "location": ["Remote", "Bangalore"], "job_type": "full_time",
    },
]


def seed_job_postings():
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()

    # Check if already seeded
    cur.execute("SELECT COUNT(*) FROM applicant.job_postings")
    if cur.fetchone()[0] > 0:
        log.info("Job postings already seeded, skipping")
        cur.close()
        conn.close()
        return

    for job in JOB_POSTINGS:
        # Compute embedding
        embed_text = f"{job['title']}. {job['description']}. Skills: {', '.join(job['required_skills'])}"
        embedding = compute_embedding(embed_text)

        cur.execute("""
            INSERT INTO applicant.job_postings
            (title, company, department, description, required_skills, preferred_skills,
             experience_min, experience_max, salary_min, salary_max, location, job_type,
             posting_embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            job["title"], job["company"], job["department"], job["description"],
            json.dumps(job["required_skills"]), json.dumps(job["preferred_skills"]),
            job["experience_min"], job["experience_max"],
            job["salary_min"], job["salary_max"],
            json.dumps(job["location"]), job["job_type"],
            str(embedding),
        ))

    log.info("Seeded %d job postings", len(JOB_POSTINGS))
    cur.close()
    conn.close()


def seed_sample_applicants():
    """Seed a few sample applicant profiles for testing."""
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM applicant.applicant_profiles")
    if cur.fetchone()[0] > 0:
        log.info("Applicant profiles already seeded, skipping")
        cur.close()
        conn.close()
        return

    applicants = [
        {
            "full_name": "Anmol Sharma", "email": "anmol.sharma@example.com",
            "desired_role": "Backend Developer", "experience_years": 3,
            "current_company": "TechCorp", "current_role": "Python Developer",
            "location_preference": ["Bangalore", "Remote"],
            "salary_min": 1800000, "salary_max": 2500000,
            "skills": [("Python", "advanced", 3), ("FastAPI", "intermediate", 2),
                       ("PostgreSQL", "intermediate", 2), ("Docker", "beginner", 1)],
        },
        {
            "full_name": "Priya Mehta", "email": "priya.mehta@example.com",
            "desired_role": "Data Scientist", "experience_years": 2,
            "current_company": "DataWorks", "current_role": "ML Intern",
            "location_preference": ["Delhi", "Bangalore"],
            "salary_min": 1200000, "salary_max": 2000000,
            "skills": [("Python", "advanced", 2), ("Pandas", "advanced", 2),
                       ("Machine Learning", "intermediate", 1), ("SQL", "intermediate", 2)],
        },
        {
            "full_name": "Rohit Verma", "email": "rohit.verma@example.com",
            "desired_role": "Full Stack Developer", "experience_years": 1,
            "current_company": None, "current_role": None,
            "location_preference": ["Bangalore"],
            "salary_min": 800000, "salary_max": 1500000,
            "skills": [("JavaScript", "intermediate", 1), ("React", "intermediate", 1),
                       ("Python", "beginner", 1), ("HTML", "intermediate", 1)],
        },
    ]

    for a in applicants:
        embed_text = f"{a['desired_role']} {' '.join(s[0] for s in a['skills'])}"
        embedding = compute_embedding(embed_text)

        cur.execute("""
            INSERT INTO applicant.applicant_profiles
            (full_name, email, desired_role, experience_years, current_company, current_role,
             location_preference, salary_min, salary_max, profile_embedding,
             onboarding_phase, profile_completion)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 2, 60)
        """, (
            a["full_name"], a["email"], a["desired_role"], a["experience_years"],
            a.get("current_company"), a.get("current_role"),
            json.dumps(a["location_preference"]),
            a["salary_min"], a["salary_max"], str(embedding),
        ))

        cur.execute("SELECT applicant_id FROM applicant.applicant_profiles WHERE email = %s",
                     (a["email"],))
        aid = cur.fetchone()[0]

        for skill_name, level, years in a["skills"]:
            cur.execute(
                "INSERT INTO applicant.skills (applicant_id, skill_name, proficiency_level, years_of_experience) "
                "VALUES (%s, %s, %s, %s)",
                (aid, skill_name, level, years),
            )

    log.info("Seeded %d sample applicants", len(applicants))
    cur.close()
    conn.close()


if __name__ == "__main__":
    log.info("=== Seeding applicant data ===")
    seed_job_postings()
    seed_sample_applicants()
    log.info("=== Applicant seed complete ===")
