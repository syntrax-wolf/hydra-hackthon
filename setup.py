"""
Horizon Platform — One-Stop Setup
Run: python setup.py

1. Installs Python dependencies
2. Creates 'horizon' database if needed
3. Runs SQL schemas (creates all tables)
4. Seeds mock data (inventory, hr, finance, onboarding, applicant)
5. Creates directories
"""
import os
import sys
import subprocess
import json
from pathlib import Path
from datetime import date, datetime, timedelta

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "")
DB_NAME = os.getenv("POSTGRES_DB", "horizon")

BASE_DIR = Path(__file__).parent
SQL_DIR = BASE_DIR / "shipathon_JMD"


def _header(text):
    print(f"\n{'='*55}")
    print(f"  {text}")
    print(f"{'='*55}")


def _step(text):
    print(f"\n--- {text} ---")


def get_conn():
    import psycopg2
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, database=DB_NAME)


# ── 1. Install dependencies ──────────────────────────────────

def install_dependencies():
    _step("Installing Python dependencies")
    req = BASE_DIR / "requirements.txt"
    if req.exists():
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("  [OK] Dependencies installed")
        else:
            print(f"  [ERROR] pip install failed:\n{result.stderr}")
    else:
        print("  [WARN] requirements.txt not found")


# ── 2. Create database ───────────────────────────────────────

def create_database():
    import psycopg2
    _step(f"Creating database '{DB_NAME}'")

    try:
        conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, database="postgres")
        conn.autocommit = True
    except Exception as e:
        print(f"  [ERROR] Cannot connect to PostgreSQL: {e}")
        print(f"  Check: PostgreSQL running at {DB_HOST}:{DB_PORT}, user='{DB_USER}'")
        sys.exit(1)

    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
    if cur.fetchone():
        print(f"  [OK] Database '{DB_NAME}' already exists")
    else:
        cur.execute(f'CREATE DATABASE "{DB_NAME}"')
        print(f"  [OK] Database '{DB_NAME}' created")
    cur.close()
    conn.close()


# ── 3. Run SQL schemas ───────────────────────────────────────

def run_sql_schemas():
    _step("Running SQL schema files")

    sql_files = ["00_extensions.sql", "01_inventory.sql", "02_hr.sql", "03_finance.sql", "04_applicant.sql"]

    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()

    for filename in sql_files:
        filepath = SQL_DIR / filename
        if not filepath.exists():
            print(f"  [SKIP] {filename} not found")
            continue
        try:
            sql = filepath.read_text(encoding="utf-8")
            cur.execute(sql)
            print(f"  [OK] {filename}")
        except Exception as e:
            err_str = str(e).strip()
            # "already exists" is fine, anything else is a real error
            if "already exists" in err_str or "duplicate" in err_str.lower():
                print(f"  [OK] {filename} (already exists)")
            else:
                print(f"  [ERROR] {filename}: {err_str}")
            # Reset connection after error
            conn.rollback()
            conn.autocommit = True

    cur.close()
    conn.close()


# ── 4. Seed base data ────────────────────────────────────────

def seed_base_data():
    _step("Seeding base data (inventory, hr, finance)")

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT count(*) FROM inventory.products")
        count = cur.fetchone()[0]
    except Exception:
        count = 0
        conn.rollback()
    cur.close()
    conn.close()

    if count > 0:
        print(f"  [SKIP] Already seeded ({count} products)")
        return

    seed_script = SQL_DIR / "seed_data.py"
    if not seed_script.exists():
        print("  [WARN] seed_data.py not found")
        return

    print("  Running seed_data.py... (this takes 1-2 minutes)")
    print("  Output below:")
    print("  " + "-" * 50)

    env = os.environ.copy()
    env["DATABASE_URL"] = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    env["POSTGRES_HOST"] = DB_HOST
    env["POSTGRES_PORT"] = str(DB_PORT)
    env["POSTGRES_USER"] = DB_USER
    env["POSTGRES_PASSWORD"] = DB_PASS
    env["POSTGRES_DB"] = DB_NAME

    # Stream output directly to terminal so user sees progress
    result = subprocess.run(
        [sys.executable, str(seed_script)],
        env=env, cwd=str(SQL_DIR),
    )
    print("  " + "-" * 50)
    if result.returncode == 0:
        print("  [OK] Base data seeded")
    else:
        print(f"  [ERROR] seed_data.py failed (exit code {result.returncode})")
        print(f"  Try running manually:")
        print(f"    set DATABASE_URL=postgresql://{DB_USER}:<password>@{DB_HOST}:{DB_PORT}/{DB_NAME}")
        print(f"    python shipathon_JMD/seed_data.py")


# ── 5. Seed onboarding data ──────────────────────────────────

def seed_onboarding_data():
    _step("Seeding onboarding mock data")

    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute("SELECT COUNT(*) FROM onboarding.onboarding_records")
        if cur.fetchone()[0] > 0:
            print("  [SKIP] Already seeded")
            cur.close()
            conn.close()
            return
    except Exception:
        conn.rollback()
        conn.autocommit = True
        print("  [WARN] onboarding tables not ready")
        cur.close()
        conn.close()
        return

    # Get managers
    try:
        cur.execute("SELECT employee_id, full_name, email_address, department FROM hr.employees WHERE designation IN ('Lead','Principal','Director') AND is_active=true ORDER BY employee_id LIMIT 5")
        managers = cur.fetchall()
    except Exception:
        managers = []
        conn.rollback()
        conn.autocommit = True

    if not managers:
        managers = [
            (1, "Priya Mehta", "priya.mehta@horizon.com", "engineering"),
            (2, "Rahul Sharma", "rahul.sharma@horizon.com", "data_science"),
            (3, "Ananya Gupta", "ananya.gupta@horizon.com", "design"),
            (4, "Vikram Singh", "vikram.singh@horizon.com", "marketing"),
            (5, "Sneha Patel", "sneha.patel@horizon.com", "sales"),
        ]

    # Manager schedules
    schedule = [
        (0,"09:00","09:30",False,"Team standup"),(0,"09:30","12:00",True,None),(0,"12:00","13:00",False,"Lunch"),
        (0,"13:00","15:00",True,None),(0,"15:00","16:00",False,"1:1s"),(0,"16:00","18:00",True,None),
        (1,"09:00","12:00",True,None),(1,"12:00","13:00",False,"Lunch"),(1,"13:00","18:00",True,None),
        (2,"09:00","10:00",False,"All-hands"),(2,"10:00","12:00",True,None),(2,"12:00","13:00",False,"Lunch"),
        (2,"13:00","18:00",True,None),(3,"09:00","12:00",True,None),(3,"12:00","13:00",False,"Lunch"),
        (3,"13:00","15:00",True,None),(3,"15:00","17:00",False,"Sprint review"),(3,"17:00","18:00",True,None),
        (4,"09:00","12:00",True,None),(4,"12:00","13:00",False,"Lunch"),(4,"13:00","16:00",True,None),
        (4,"16:00","18:00",False,"Team social"),(5,"09:00","18:00",False,"Weekend"),(6,"09:00","18:00",False,"Weekend"),
    ]
    for mgr in managers:
        email = mgr[2] or f"{mgr[1].lower().replace(' ','.')}@horizon.com"
        for day, s, e, avail, label in schedule:
            cur.execute("INSERT INTO onboarding.manager_schedule (manager_email,day_of_week,start_time,end_time,is_available,block_label) VALUES (%s,%s,%s,%s,%s,%s)", (email,day,s,e,avail,label))

    dept_sys = {"engineering":["email","slack","github","jira","confluence"],"data_science":["email","slack","github","jira","jupyter"],"design":["email","slack","figma","jira","confluence"],"marketing":["email","slack","hubspot","canva","analytics"],"sales":["email","slack","hubspot","crm","analytics"],"finance_ops":["email","slack","erp","jira"],"hr_admin":["email","slack","hrms","jira"],"product":["email","slack","jira","confluence","figma"]}

    emps = [
        ("Arjun Nair","arjun.nair@horizon.com","engineering","Senior Associate","Mumbai","complete",6),
        ("Kavita Reddy","kavita.reddy@horizon.com","data_science","Associate","Bangalore","complete",6),
        ("Rohan Das","rohan.das@horizon.com","design","Junior Associate","Kolkata","complete",6),
        ("Meera Iyer","meera.iyer@horizon.com","marketing","Associate","Chennai","complete",6),
        ("Aditya Joshi","aditya.joshi@horizon.com","sales","Senior Associate","Pune","complete",6),
        ("Neha Kapoor","neha.kapoor@horizon.com","engineering","Associate","Delhi","email_reviewed",2),
        ("Siddharth Malhotra","siddharth.malhotra@horizon.com","product","Senior Associate","Mumbai","scheduled",3),
        ("Pooja Bhatt","pooja.bhatt@horizon.com","finance_ops","Junior Associate","Hyderabad","doc_generated",4),
        ("Raj Kumar","raj.kumar@horizon.com","hr_admin","Associate","Delhi","failed",2),
        ("Deepa Menon","deepa.menon@horizon.com","data_science","Senior Associate","Bangalore","failed",5),
        ("Amit Trivedi","amit.trivedi@horizon.com","engineering","Intern","Mumbai","pending",0),
        ("Shreya Ghosh","shreya.ghosh@horizon.com","design","Junior Associate","Kolkata","pending",0),
        ("Varun Khanna","varun.khanna@horizon.com","sales","Associate","Delhi","pending",0),
        ("Priyanka Sen","priyanka.sen@horizon.com","marketing","Intern","Chennai","pending",0),
        ("Karthik Rajan","karthik.rajan@horizon.com","product","Associate","Bangalore","pending",0),
    ]

    for i,(name,email,dept,desg,region,status,step) in enumerate(emps):
        mgr=managers[i%len(managers)]; buddy=managers[(i+1)%len(managers)]
        mn,me=mgr[1],mgr[2] or f"{mgr[1].lower().replace(' ','.')}@horizon.com"
        bn,be=buddy[1],buddy[2] or f"{buddy[1].lower().replace(' ','.')}@horizon.com"
        sd=date(2026,3,1)+timedelta(days=i*3)
        ca=(datetime(2026,3,10)+timedelta(days=i*2)) if status=="complete" else None
        fs=2 if(status=="failed" and step==2) else(5 if status=="failed" else None)
        em="SMTP timeout" if fs==2 else("PDF generation failed" if fs==5 else None)
        es="sent" if status in("complete","email_reviewed","scheduled","doc_generated") else "pending"
        aj="[]"
        if status!="pending":
            systems=dept_sys.get(dept,["email","slack"])
            aj=json.dumps([{"system":s,"account_id":f"{name.split()[0].lower()}.{name.split()[-1].lower()}@{s}.horizon.com"} for s in systems])
        mt=datetime(2026,3,15,10,0)+timedelta(days=i) if status in("complete","scheduled","doc_generated") else None
        dp=f"generated/onboarding_{name.lower().replace(' ','_')}.pdf" if status in("complete","doc_generated") else None
        cur.execute("INSERT INTO onboarding.onboarding_records (employee_name,employee_email,department,designation,region,manager_name,manager_email,buddy_name,buddy_email,start_date,status,current_step,failed_at_step,error_message,accounts_provisioned,welcome_email_status,kickoff_meeting_time,onboarding_doc_path,completed_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING onboarding_id",
                    (name,email,dept,desg,region,mn,me,bn,be,sd,status,step,fs,em,aj,es,mt,dp,ca))
        oid=cur.fetchone()[0]
        if status!="pending":
            f,l=name.split()[0].lower(),name.split()[-1].lower()
            for sn in dept_sys.get(dept,["email","slack"]):
                aid=f"{f}.{l}@horizon.com" if sn=="email" else f"@{f}.{l}" if sn=="slack" else f"github.com/{f}-{l}" if sn=="github" else f"{f}.{l}@{sn}.horizon.com"
                cur.execute("INSERT INTO onboarding.system_accounts(onboarding_id,system_name,account_identifier) VALUES(%s,%s,%s)",(oid,sn,aid))
        if status=="complete":
            cur.execute("INSERT INTO onboarding.email_drafts(onboarding_id,draft_number,email_body) VALUES(%s,1,%s)",(oid,f"Dear {name.split()[0]},\n\nWelcome to Horizon!\n\nBest,\nHR Team"))

    print(f"  [OK] Seeded {len(emps)} onboarding records")
    cur.close()
    conn.close()


# ── 6. Seed applicant data ───────────────────────────────────

def seed_applicant_data():
    _step("Seeding applicant data (job postings)")

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT count(*) FROM applicant.job_postings")
        count = cur.fetchone()[0]
    except Exception:
        count = 0
        conn.rollback()
    cur.close()
    conn.close()

    if count > 0:
        print(f"  [SKIP] Already seeded ({count} job postings)")
        return

    seed_script = SQL_DIR / "seed_applicant_data.py"
    if not seed_script.exists():
        print("  [WARN] seed_applicant_data.py not found")
        return

    print("  Running seed_applicant_data.py...")
    print("  " + "-" * 50)

    env = os.environ.copy()
    env["POSTGRES_HOST"] = DB_HOST
    env["POSTGRES_PORT"] = str(DB_PORT)
    env["POSTGRES_USER"] = DB_USER
    env["POSTGRES_PASSWORD"] = DB_PASS
    env["POSTGRES_DB"] = DB_NAME

    result = subprocess.run([sys.executable, str(seed_script)], env=env, cwd=str(SQL_DIR))
    print("  " + "-" * 50)
    if result.returncode == 0:
        print("  [OK] Applicant data seeded")
    else:
        print(f"  [ERROR] seed_applicant_data.py failed (exit code {result.returncode})")


# ── 7. Validate everything ───────────────────────────────────

def validate_all():
    _step("Final validation — row counts")

    conn = get_conn()
    cur = conn.cursor()

    tables = [
        ("inventory", "products", 75), ("inventory", "warehouses", 7),
        ("inventory", "inventory_levels", 525), ("inventory", "stock_movements", 5000),
        ("inventory", "product_pricing", 75), ("inventory", "price_history", 1497),
        ("hr", "employees", 100), ("hr", "employee_skills", 500),
        ("hr", "performance_reviews", 200), ("hr", "leave_records", 300),
        ("finance", "offices", 7), ("finance", "sales_transactions", 10000),
        ("onboarding", "onboarding_records", 15), ("onboarding", "manager_schedule", 100),
        ("applicant", "applicant_profiles", 3), ("applicant", "job_postings", 20),
    ]

    empty_count = 0
    for schema, table, expected in tables:
        try:
            cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
            count = cur.fetchone()[0]
            if count == 0:
                print(f"  [EMPTY] {schema}.{table} — 0 rows (expected ~{expected})")
                empty_count += 1
            else:
                print(f"  [OK]    {schema}.{table} — {count} rows")
        except Exception as e:
            print(f"  [ERROR] {schema}.{table} — {e}")
            empty_count += 1
            conn.rollback()

    # Materialized views
    for view in ["mv_daily_office_profit_loss", "mv_daily_product_revenue"]:
        try:
            cur.execute(f'SELECT count(*) FROM finance."{view}"')
            count = cur.fetchone()[0]
            status = "[OK]   " if count > 0 else "[EMPTY]"
            print(f"  {status} finance.{view} — {count} rows")
        except Exception:
            print(f"  [ERROR] finance.{view} — not found")
            conn.rollback()

    cur.close()
    conn.close()

    if empty_count > 0:
        print(f"\n  [WARN] {empty_count} table(s) are empty. Check errors above.")
    else:
        print(f"\n  [OK] All tables have data!")


# ── 8. Directories ────────────────────────────────────────────

def ensure_dirs():
    _step("Creating directories")
    for d in ["generated", "resumes"]:
        (BASE_DIR / d).mkdir(exist_ok=True)
        print(f"  [OK] {d}/")


# ── Main ──────────────────────────────────────────────────────

def main():
    _header("Horizon Platform — Setup")

    install_dependencies()
    create_database()
    run_sql_schemas()
    seed_base_data()
    seed_onboarding_data()
    seed_applicant_data()
    validate_all()
    ensure_dirs()

    _header("Setup Complete!")
    print("\nRun 'python service.py' to start the server.\n")


if __name__ == "__main__":
    main()
