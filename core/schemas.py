from pydantic import BaseModel
from typing import Literal, Optional


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class QueryRequest(BaseModel):
    query: str
    format: str = "auto"
    conversation_history: list[ConversationTurn] = []


# ── Onboarding schemas ──

class OnboardingStartRequest(BaseModel):
    employee_name: str
    employee_email: Optional[str] = None
    department: str
    designation: str
    region: Optional[str] = None
    manager_email: Optional[str] = None
    buddy_name: Optional[str] = None
    buddy_email: Optional[str] = None
    start_date: str


class EmailActionRequest(BaseModel):
    action: Literal["send", "revise", "skip"]
    feedback: Optional[str] = None


class SlotSelectionRequest(BaseModel):
    slot_index: int


class EmployeeSelectionRequest(BaseModel):
    onboarding_id: int


class DataRequirement(BaseModel):
    req_id: str
    table: Literal[
        "inventory.products", "inventory.warehouses", "inventory.inventory_levels",
        "inventory.stock_movements", "inventory.product_pricing", "inventory.price_history",
        "hr.employees", "hr.employee_skills", "hr.performance_reviews", "hr.leave_records",
        "finance.offices", "finance.sales_transactions",
        "finance.mv_daily_office_profit_loss", "finance.mv_daily_product_revenue",
    ]
    columns: list[str]
    filters: dict = {}
    group_by: list[str] = []
    order_by: str | None = None
    aggregate: dict = {}
    priority: Literal["required", "nice_to_have"] = "required"


class Finding(BaseModel):
    finding: str
    sentiment: Literal["positive", "negative", "neutral", "warning"]
    metric: str


class Recommendation(BaseModel):
    action: str
    priority: Literal["critical", "high", "medium", "low"]
    impact: str = ""


class Narrative(BaseModel):
    executive_summary: str
    detailed_analysis: str = ""
    key_findings: list[Finding]
    recommendations: list[Recommendation]
    caveats: list[str] = []


class PipelineResponse(BaseModel):
    query_id: str
    status: Literal["complete", "error"]
    narrative: Narrative | None = None
    file: dict | None = None
    follow_ups: list[str] = []
    time_ms: int = 0
    error: str | None = None


# ── Applicant schemas ──

class ApplicantAuthRequest(BaseModel):
    email: str
    full_name: Optional[str] = None


class ApplicantQueryRequest(BaseModel):
    message: str
    conversation_history: list[ConversationTurn] = []


class ProfileConfirmRequest(BaseModel):
    """Data from a confirmation card Save action."""
    profile: dict = {}
    skills: list[dict] = []
    education: list[dict] = []
    experience: list[dict] = []


class SkillRequest(BaseModel):
    skill_name: str
    proficiency_level: str = "intermediate"
    years_of_experience: int = 0


class EducationRequest(BaseModel):
    institution: str
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    gpa_grade: Optional[str] = None


class ExperienceRequest(BaseModel):
    company_name: str
    role_title: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_current: bool = False
    description: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    headline: Optional[str] = None
    summary: Optional[str] = None
    desired_role: Optional[str] = None
    desired_department: Optional[str] = None
    experience_years: Optional[int] = None
    current_company: Optional[str] = None
    current_role: Optional[str] = None
    location_preference: Optional[list[str]] = None
    willing_to_relocate: Optional[bool] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    job_type_preference: Optional[list[str]] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None


class ApplicationSubmitRequest(BaseModel):
    job_id: int
    cover_letter: str
    match_score: int = 0
