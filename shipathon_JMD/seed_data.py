"""
seed_data.py — Populate Nasiko RAG Pipeline database with realistic synthetic data.

Volumes (adjusted):
  75 products, 7 warehouses, 7 offices, 525 inventory_levels,
  5000 stock_movements, 75 product_pricing, 1500 price_history,
  100 employees, ~500 employee_skills, 200 performance_reviews,
  300 leave_records, 10000 sales_transactions.

Usage:
  set DATABASE_URL=postgresql://postgres:<password>@localhost:5432/horizon
  python seed_data.py
"""

import asyncio
import asyncpg
import os
import random
import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from faker import Faker

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")
fake = Faker("en_IN")
Faker.seed(42)
random.seed(42)
np.random.seed(42)

EMBEDDING_DIM = 1024
USE_REAL_EMBEDDINGS = True  # set False for fast placeholder vectors

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CATEGORIES: dict[str, list[str]] = {
    "electronics": [
        "smartphones", "laptops", "tablets", "headphones", "chargers",
        "smartwatches", "cables", "speakers", "cameras", "monitors",
    ],
    "clothing": [
        "shirts", "trousers", "dresses", "jackets", "shoes",
        "socks", "belts", "scarves", "caps", "sunglasses",
    ],
    "food_beverages": [
        "snacks", "beverages", "dairy", "grains", "spices",
        "frozen", "canned", "bakery", "sauces", "oils",
    ],
    "pharma_health": [
        "painkillers", "vitamins", "sanitizers", "bandages", "thermometers",
        "masks", "supplements", "syrups", "creams", "drops",
    ],
    "home_office": [
        "furniture", "stationery", "lighting", "storage", "cleaning",
        "tools", "decoration", "kitchenware", "bedding", "curtains",
    ],
}

# 15 products per category = 75 total
PRODUCTS_PER_CATEGORY = 15

CITIES = ["Mumbai", "Delhi", "Bangalore", "Hyderabad", "Chennai", "Pune", "Kolkata"]
CITY_STATES = {
    "Mumbai": "Maharashtra",
    "Delhi": "Delhi",
    "Bangalore": "Karnataka",
    "Hyderabad": "Telangana",
    "Chennai": "Tamil Nadu",
    "Pune": "Maharashtra",
    "Kolkata": "West Bengal",
}

DEPARTMENTS: dict[str, float] = {
    "engineering": 0.30,
    "data_science": 0.10,
    "product": 0.10,
    "design": 0.08,
    "marketing": 0.12,
    "sales": 0.12,
    "hr_admin": 0.08,
    "finance_ops": 0.10,
}

DEPT_SKILLS: dict[str, list[str]] = {
    "engineering": [
        "Python", "Java", "JavaScript", "TypeScript", "React", "Node.js",
        "PostgreSQL", "Docker", "Kubernetes", "AWS", "GCP", "Git",
        "REST APIs", "GraphQL", "CI/CD", "Redis", "MongoDB",
        "System Design", "Microservices", "Linux",
    ],
    "data_science": [
        "Python", "R", "SQL", "TensorFlow", "PyTorch", "Pandas", "NumPy",
        "Scikit-learn", "Statistics", "Machine Learning", "Deep Learning",
        "NLP", "Computer Vision", "Data Visualization", "Spark", "Hadoop",
        "Jupyter", "A/B Testing",
    ],
    "product": [
        "Product Strategy", "Roadmapping", "User Research", "A/B Testing",
        "Jira", "Figma", "SQL", "Data Analysis", "Stakeholder Management",
        "Agile", "Scrum", "PRD Writing", "Competitive Analysis", "Wireframing",
    ],
    "design": [
        "Figma", "Sketch", "Adobe XD", "Illustrator", "Photoshop",
        "UI Design", "UX Research", "Prototyping", "Design Systems",
        "Typography", "Color Theory", "Interaction Design", "Accessibility",
        "Motion Design",
    ],
    "marketing": [
        "SEO", "SEM", "Google Analytics", "Content Strategy", "Copywriting",
        "Social Media", "Email Marketing", "Marketing Automation",
        "Brand Strategy", "PR", "Influencer Marketing", "CRM", "HubSpot",
        "Paid Ads",
    ],
    "sales": [
        "CRM", "Salesforce", "Lead Generation", "Cold Calling", "Negotiation",
        "Pipeline Management", "Account Management", "Presentation Skills",
        "Consultative Selling", "Revenue Forecasting",
    ],
    "hr_admin": [
        "Recruitment", "Onboarding", "Performance Management", "Payroll",
        "Compliance", "Employee Engagement", "Training", "HRIS",
        "Labour Law", "Conflict Resolution", "Benefits Administration",
    ],
    "finance_ops": [
        "Financial Modelling", "Excel", "SAP", "Tally", "GST", "Accounting",
        "Budgeting", "Forecasting", "Audit", "Tax Planning",
        "Accounts Payable", "Accounts Receivable", "Cost Analysis", "Treasury",
    ],
}

SKILL_CATEGORIES: dict[str, str] = {}
_cat_map = {
    "programming": [
        "Python", "Java", "JavaScript", "TypeScript", "React", "Node.js",
        "R", "SQL", "GraphQL", "REST APIs",
    ],
    "cloud": ["AWS", "GCP", "Docker", "Kubernetes", "Linux", "CI/CD"],
    "data": [
        "TensorFlow", "PyTorch", "Pandas", "NumPy", "Scikit-learn",
        "Spark", "Hadoop", "Jupyter", "Statistics", "Machine Learning",
        "Deep Learning", "NLP", "Computer Vision", "Data Visualization",
        "A/B Testing", "Data Analysis",
    ],
    "database": ["PostgreSQL", "Redis", "MongoDB"],
    "design": [
        "Figma", "Sketch", "Adobe XD", "Illustrator", "Photoshop",
        "UI Design", "UX Research", "Prototyping", "Design Systems",
        "Typography", "Color Theory", "Interaction Design", "Accessibility",
        "Motion Design", "Wireframing",
    ],
    "management": [
        "Product Strategy", "Roadmapping", "User Research", "Jira",
        "Stakeholder Management", "Agile", "Scrum", "PRD Writing",
        "Competitive Analysis", "System Design", "Microservices", "Git",
    ],
    "marketing": [
        "SEO", "SEM", "Google Analytics", "Content Strategy", "Copywriting",
        "Social Media", "Email Marketing", "Marketing Automation",
        "Brand Strategy", "PR", "Influencer Marketing", "HubSpot", "Paid Ads",
    ],
    "sales": [
        "CRM", "Salesforce", "Lead Generation", "Cold Calling", "Negotiation",
        "Pipeline Management", "Account Management", "Presentation Skills",
        "Consultative Selling", "Revenue Forecasting",
    ],
    "hr": [
        "Recruitment", "Onboarding", "Performance Management", "Payroll",
        "Compliance", "Employee Engagement", "Training", "HRIS",
        "Labour Law", "Conflict Resolution", "Benefits Administration",
    ],
    "finance": [
        "Financial Modelling", "Excel", "SAP", "Tally", "GST", "Accounting",
        "Budgeting", "Forecasting", "Audit", "Tax Planning",
        "Accounts Payable", "Accounts Receivable", "Cost Analysis", "Treasury",
    ],
}
for cat, skills in _cat_map.items():
    for s in skills:
        SKILL_CATEGORIES[s] = cat

DESIGNATIONS = [
    ("Intern", "P1", 0.08),
    ("Junior Associate", "P2", 0.20),
    ("Associate", "P3", 0.25),
    ("Senior Associate", "P4", 0.22),
    ("Lead", "P5", 0.15),
    ("Principal", "P6", 0.07),
    ("Director", "P7", 0.03),
]

SALARY_BANDS: dict[str, tuple[int, int]] = {
    "P1": (200_000, 400_000),
    "P2": (400_000, 800_000),
    "P3": (800_000, 1_500_000),
    "P4": (1_500_000, 2_500_000),
    "P5": (2_500_000, 4_000_000),
    "P6": (4_000_000, 6_000_000),
    "P7": (6_000_000, 8_000_000),
}

DEPT_SALARY_MULT: dict[str, float] = {
    "engineering": 1.30,
    "data_science": 1.25,
    "product": 1.10,
    "design": 1.05,
    "marketing": 1.00,
    "sales": 1.05,
    "hr_admin": 0.85,
    "finance_ops": 1.00,
}

COST_RANGES: dict[str, tuple[float, float]] = {
    "electronics": (500, 80_000),
    "clothing": (200, 5_000),
    "food_beverages": (20, 500),
    "pharma_health": (30, 2_000),
    "home_office": (100, 15_000),
}

MARGIN_RANGES: dict[str, tuple[float, float]] = {
    "electronics": (0.15, 0.40),
    "clothing": (0.30, 0.60),
    "food_beverages": (0.05, 0.20),
    "pharma_health": (0.10, 0.35),
    "home_office": (0.20, 0.50),
}

ELASTICITY_RANGES: dict[str, tuple[float, float]] = {
    "electronics": (-2.0, -0.8),
    "clothing": (-2.5, -1.0),
    "food_beverages": (-0.8, -0.3),
    "pharma_health": (-1.0, -0.3),
    "home_office": (-1.8, -0.8),
}

PRODUCT_ADJECTIVES: dict[str, list[str]] = {
    "electronics": ["ProMax", "UltraSlim", "NeoEdge", "TechPro", "SmartElite", "PowerX", "ZenTech", "QuantumX", "VoltEdge", "NanoTech", "CyberPro", "HyperLink", "DigiMax", "InfinityX", "ByteForce"],
    "clothing": ["RoyalFit", "UrbanStyle", "ClassicWear", "TrendSet", "ElegantPlus", "ComfortFlex", "PrimeLine", "FashionEdge", "StyleCraft", "ModernWear", "EliteThread", "VogueBlend", "ChicForm", "SlimFit", "DailyWear"],
    "food_beverages": ["FreshFarm", "NaturePure", "GoldenHarvest", "OrganicBest", "TastyBite", "PureDesi", "HomeMade", "RoyalTaste", "FreshPick", "DailyFresh", "VitalNutra", "PrimeChoice", "GoodLife", "NutriRich", "FlavorKing"],
    "pharma_health": ["MediCare", "HealthPlus", "VitaGuard", "WellCure", "PureMed", "HealFast", "BioShield", "SafeGuard", "NutraLife", "CareFirst", "MediPrime", "HealthEdge", "VitalCure", "WellnessX", "ImmunoPlus"],
    "home_office": ["HomeElite", "OfficePro", "ComfortZone", "SmartSpace", "DeskMaster", "EcoHome", "PrimeDecor", "WorkEase", "CleanMax", "CraftLine", "SpaceSaver", "NeatHome", "UrbanLiving", "CozyNest", "BrightSpace"],
}

PAYMENT_METHODS = ["UPI", "credit_card", "debit_card", "cash", "net_banking"]

MOVEMENT_TYPES = [
    ("outbound", 0.45),
    ("inbound", 0.30),
    ("transfer_out", 0.08),
    ("transfer_in", 0.08),
    ("adjustment", 0.05),
    ("return", 0.04),
]

DATE_START = date(2025, 9, 1)
DATE_END = date(2026, 2, 28)

# ---------------------------------------------------------------------------
# Embedding helper
# ---------------------------------------------------------------------------
_model = None


def get_model():
    global _model
    if _model is None:
        if USE_REAL_EMBEDDINGS:
            from sentence_transformers import SentenceTransformer
            print("Loading BGE-M3 model (first time may download ~2GB)...")
            _model = SentenceTransformer("BAAI/bge-m3")
            print("Model loaded.")
        else:
            _model = "placeholder"
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch-encode texts to 1024-dim embeddings."""
    model = get_model()
    if model == "placeholder":
        # Normalised random vectors as placeholder
        vecs = np.random.randn(len(texts), EMBEDDING_DIM).astype(np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / norms
        return vecs.tolist()
    else:
        vecs = model.encode(
            texts,
            batch_size=64,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        # Truncate or pad to 1024 dims
        if vecs.shape[1] > EMBEDDING_DIM:
            vecs = vecs[:, :EMBEDDING_DIM]
        elif vecs.shape[1] < EMBEDDING_DIM:
            pad = np.zeros((vecs.shape[0], EMBEDDING_DIM - vecs.shape[1]))
            vecs = np.hstack([vecs, pad])
        return vecs.tolist()


def vec_to_pg(vec: list[float]) -> str:
    """Convert a list of floats to PostgreSQL vector literal."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def random_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def generate_product_description(adj: str, subcat: str, category: str) -> str:
    templates = [
        f"Premium {adj} {subcat} designed for modern lifestyles. Built with high-quality materials for exceptional durability and performance in the {category} segment.",
        f"{adj} {subcat} offering the perfect blend of quality and value. Ideal for everyday use with advanced features tailored for Indian consumers in the {category} category.",
        f"Top-rated {adj} {subcat} featuring cutting-edge technology and elegant design. A bestseller in the {category} range with excellent customer reviews.",
        f"Reliable {adj} {subcat} crafted for Indian households. Combines functionality with style in the {category} product line. Backed by comprehensive warranty.",
        f"Innovative {adj} {subcat} setting new standards in the {category} market. Features ergonomic design and eco-friendly materials for conscious consumers.",
    ]
    return random.choice(templates)


def generate_review_text(
    name: str, dept: str, period: str, rating: float, skills: list[str]
) -> str:
    if rating >= 4.5:
        quality = random.choice(["outstanding", "exceptional", "exemplary"])
        achievement = f"{name} consistently exceeded expectations and delivered results that significantly impacted the team's objectives."
        recommend = "Strongly recommended for accelerated promotion and leadership roles."
    elif rating >= 3.5:
        quality = random.choice(["strong", "commendable", "solid", "good"])
        achievement = f"{name} met all key deliverables and showed consistent improvement throughout the review period."
        recommend = "Encouraged to take on more challenging projects and mentor junior team members."
    elif rating >= 2.5:
        quality = random.choice(["satisfactory", "adequate", "acceptable"])
        achievement = f"{name} delivered on core responsibilities but could benefit from more proactive engagement."
        recommend = "Advised to seek additional training and set more ambitious goals for the next cycle."
    else:
        quality = random.choice(["below expectations", "needs improvement"])
        achievement = f"{name} struggled to meet some key targets and requires focused development in critical areas."
        recommend = "A performance improvement plan is recommended with monthly check-ins."

    skill_mention = ""
    if skills:
        picked = random.sample(skills, min(2, len(skills)))
        skill_mention = f" Their proficiency in {' and '.join(picked)} has been particularly valuable for the team's deliverables."

    return (
        f"{name} has demonstrated {quality} performance in the {dept} department during {period}."
        f"{skill_mention} {achievement} {recommend}"
    )


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------
async def seed_products(conn) -> list[dict]:
    """Seed 75 products (15 per category). Returns product metadata."""
    print("Seeding products...")
    products = []
    for cat, subcats in CATEGORIES.items():
        adjs = PRODUCT_ADJECTIVES[cat]
        chosen_subcats = random.sample(subcats, min(PRODUCTS_PER_CATEGORY, len(subcats)))
        # If we need more than unique subcats, cycle
        while len(chosen_subcats) < PRODUCTS_PER_CATEGORY:
            chosen_subcats.append(random.choice(subcats))

        for i, subcat in enumerate(chosen_subcats):
            adj = adjs[i % len(adjs)]
            sku = f"{cat[:3].upper()}-{subcat[:3].upper()}-{len(products)+1:04d}"
            name = f"{adj} {subcat.title()}"
            desc = generate_product_description(adj, subcat, cat)
            uom = "kg" if cat == "food_beverages" else "pcs"
            products.append({
                "sku": sku,
                "name": name,
                "desc": desc,
                "category": cat,
                "subcategory": subcat,
                "uom": uom,
            })

    records = [
        (p["sku"], p["name"], p["desc"], p["category"], p["subcategory"], p["uom"], True)
        for p in products
    ]
    await conn.copy_records_to_table(
        "products",
        records=records,
        columns=[
            "stock_keeping_unit", "product_name", "product_description",
            "category", "subcategory", "unit_of_measure", "is_active",
        ],
        schema_name="inventory",
    )

    # Fetch assigned IDs
    rows = await conn.fetch(
        "SELECT product_id, stock_keeping_unit FROM inventory.products ORDER BY product_id"
    )
    id_map = {r["stock_keeping_unit"]: r["product_id"] for r in rows}
    for p in products:
        p["product_id"] = id_map[p["sku"]]

    print(f"  {len(products)} products inserted.")
    return products


async def seed_warehouses(conn) -> list[int]:
    """Seed 7 warehouses. Returns warehouse_ids in city order."""
    print("Seeding warehouses...")
    wh_types = ["warehouse", "warehouse", "store", "store", "warehouse", "store", "office"]
    capacities = [45000, 40000, 15000, 12000, 35000, 18000, 8000]
    ids = []
    for i, city in enumerate(CITIES):
        row = await conn.fetchrow(
            """INSERT INTO inventory.warehouses
               (warehouse_name, city, state, warehouse_type, capacity_square_feet)
               VALUES ($1, $2, $3, $4, $5) RETURNING warehouse_id""",
            f"Nasiko {city} Warehouse",
            city,
            CITY_STATES[city],
            wh_types[i],
            capacities[i],
        )
        ids.append(row["warehouse_id"])
    print(f"  {len(ids)} warehouses inserted.")
    return ids


async def seed_offices(conn) -> list[int]:
    """Seed 7 offices matching warehouse cities. Returns office_ids."""
    print("Seeding offices...")
    office_types = ["headquarters", "branch", "branch", "branch", "branch", "store", "store"]
    capitals = [5_00_00_000, 2_00_00_000, 1_50_00_000, 1_00_00_000, 80_00_000, 50_00_000, 40_00_000]
    monthly_opex = [80_00_000, 30_00_000, 25_00_000, 20_00_000, 15_00_000, 10_00_000, 8_00_000]
    ids = []
    for i, city in enumerate(CITIES):
        cap = capitals[i]
        opex = monthly_opex[i]
        ar = round(cap * random.uniform(0.05, 0.15), 2)
        inv_val = round(cap * random.uniform(0.10, 0.25), 2)
        cash = round(cap * random.uniform(0.02, 0.08), 2)
        ap = round(cap * random.uniform(0.03, 0.10), 2)
        row = await conn.fetchrow(
            """INSERT INTO finance.offices
               (office_name, city, state, office_type, date_opened, operational_status,
                one_time_capital_invested, monthly_operating_expense,
                operating_expense_period_month,
                accounts_receivable_amount, inventory_value_amount,
                cash_on_hand_amount, accounts_payable_amount,
                working_capital_period_month)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
               RETURNING office_id""",
            f"Nasiko {city} Office", city, CITY_STATES[city], office_types[i],
            random_date(date(2018, 1, 1), date(2022, 12, 31)), "active",
            Decimal(str(cap)), Decimal(str(opex)), date(2026, 3, 1),
            Decimal(str(ar)), Decimal(str(inv_val)),
            Decimal(str(cash)), Decimal(str(ap)), date(2026, 3, 1),
        )
        ids.append(row["office_id"])
    print(f"  {len(ids)} offices inserted.")
    return ids


async def seed_inventory_levels(conn, products: list[dict], warehouse_ids: list[int]):
    """Seed inventory_levels: every product × every warehouse."""
    print("Seeding inventory levels...")
    records = []
    for p in products:
        cat = p["category"]
        base_safety = {"electronics": 80, "clothing": 60, "food_beverages": 40,
                       "pharma_health": 50, "home_office": 30}[cat]
        for wid in warehouse_ids:
            safety = random.randint(base_safety - 10, base_safety + 20)
            reorder = int(safety * 1.5)
            reorder_qty = int(safety * 2)
            max_stock = safety * 5
            # 80% healthy, 15% below safety, 5% dead
            r = random.random()
            if r < 0.05:
                current = 0
            elif r < 0.20:
                current = random.randint(1, max(1, safety - 1))
            else:
                current = random.randint(reorder, max_stock)
            records.append((
                p["product_id"], wid, current, safety,
                reorder, reorder_qty, max_stock,
            ))
    await conn.copy_records_to_table(
        "inventory_levels",
        records=records,
        columns=[
            "product_id", "warehouse_id", "current_quantity",
            "safety_stock_quantity", "reorder_point_quantity",
            "reorder_order_quantity", "maximum_stock_quantity",
        ],
        schema_name="inventory",
    )
    print(f"  {len(records)} inventory level records inserted.")


async def seed_stock_movements(
    conn, products: list[dict], warehouse_ids: list[int]
):
    """Seed ~5000 stock movements over 6 months with seasonal patterns."""
    print("Seeding stock movements...")
    total_days = (DATE_END - DATE_START).days + 1

    # Popularity weights (power-law)
    n = len(products)
    weights = np.random.zipf(a=1.5, size=n).astype(float)
    weights = weights / weights.sum()

    move_types = [m[0] for m in MOVEMENT_TYPES]
    move_probs = [m[1] for m in MOVEMENT_TYPES]

    # Build cost lookup
    cost_lookup = {}
    for p in products:
        lo, hi = COST_RANGES[p["category"]]
        cost_lookup[p["product_id"]] = round(random.uniform(lo, hi), 2)

    records = []
    target = 5000
    per_day = target / total_days

    for day_offset in range(total_days):
        d = DATE_START + timedelta(days=day_offset)
        month = d.month
        # Seasonal multiplier
        if month == 11:
            mult = 1.5
        elif month == 12:
            mult = 2.0
        elif month == 1:
            mult = 0.8
        else:
            mult = 1.0
        count = max(1, int(per_day * mult + random.gauss(0, 2)))
        for _ in range(count):
            pid = int(np.random.choice([p["product_id"] for p in products], p=weights))
            wid = random.choice(warehouse_ids)
            mtype = random.choices(move_types, weights=move_probs, k=1)[0]
            qty = random.randint(1, 80) if mtype in ("inbound", "outbound") else random.randint(1, 30)
            ref = f"{'PO' if mtype == 'inbound' else 'SO'}-{fake.bothify('####??').upper()}"
            cost = cost_lookup.get(pid, 100.0)
            ts = datetime.combine(d, datetime.min.time()) + timedelta(
                hours=random.randint(8, 20), minutes=random.randint(0, 59)
            )
            records.append((pid, wid, mtype, qty, ref, Decimal(str(cost)), ts))

    # Trim or pad to target
    if len(records) > target:
        records = random.sample(records, target)

    await conn.copy_records_to_table(
        "stock_movements",
        records=records,
        columns=[
            "product_id", "warehouse_id", "movement_type", "quantity",
            "reference_identifier", "unit_cost_at_movement", "moved_at",
        ],
        schema_name="inventory",
    )
    print(f"  {len(records)} stock movements inserted.")
    return cost_lookup


async def seed_product_pricing(conn, products: list[dict], cost_lookup: dict[int, float]):
    """Seed product_pricing: 1 per product."""
    print("Seeding product pricing...")
    records = []
    for p in products:
        cat = p["category"]
        cost = cost_lookup[p["product_id"]]
        margin = random.uniform(*MARGIN_RANGES[cat])
        selling = round(cost / (1 - margin), 2)
        base_selling = round(selling * random.uniform(0.95, 1.05), 2)
        floor_price = round(cost * 1.05, 2)
        ceiling_price = round(selling * 1.20, 2)

        # Competitor prices
        n_comp = random.randint(3, 8)
        comp_prices = [round(selling * random.uniform(0.85, 1.15), 2) for _ in range(n_comp)]
        comp_min = min(comp_prices)
        comp_max = max(comp_prices)
        comp_avg = round(sum(comp_prices) / len(comp_prices), 2)

        elasticity = round(random.uniform(*ELASTICITY_RANGES[cat]), 3)
        normal_units = round(random.uniform(5, 200), 2)
        sale_mult = random.uniform(1.5, 3.0)
        sale_units = round(normal_units * sale_mult, 2)
        sale_discount = round(selling * random.uniform(0.75, 0.90), 2)

        records.append((
            p["product_id"],
            Decimal(str(cost)), Decimal(str(base_selling)),
            Decimal(str(selling)), Decimal(str(floor_price)),
            Decimal(str(ceiling_price)),
            Decimal(str(comp_min)), Decimal(str(comp_max)),
            Decimal(str(comp_avg)), n_comp,
            random_date(date(2026, 1, 1), date(2026, 3, 15)),
            Decimal(str(elasticity)),
            Decimal(str(normal_units)), Decimal(str(sale_units)),
            Decimal(str(sale_discount)),
            random_date(date(2025, 11, 1), date(2026, 2, 28)),
        ))

    await conn.copy_records_to_table(
        "product_pricing",
        records=records,
        columns=[
            "product_id", "cost_price_per_unit", "base_selling_price_per_unit",
            "current_selling_price_per_unit", "floor_price_per_unit",
            "ceiling_price_per_unit",
            "competitor_minimum_price", "competitor_maximum_price",
            "competitor_average_price", "number_of_competitors_tracked",
            "last_competitor_check_date",
            "demand_elasticity_coefficient",
            "average_daily_units_normal_day", "average_daily_units_sale_day",
            "typical_sale_discount_price", "last_sale_event_date",
        ],
        schema_name="inventory",
    )
    print(f"  {len(records)} product pricing records inserted.")


async def seed_price_history(conn, products: list[dict], cost_lookup: dict[int, float]):
    """Seed ~1500 price history records (~20 per product)."""
    print("Seeding price history...")
    records = []
    price_types = ["our_price", "cost_price", "competitor_average",
                   "competitor_minimum", "market_lowest"]
    pt_weights = [0.40, 0.20, 0.20, 0.10, 0.10]

    for p in products:
        cost = cost_lookup[p["product_id"]]
        margin = random.uniform(*MARGIN_RANGES[p["category"]])
        selling = cost / (1 - margin)
        n_entries = random.randint(15, 25)
        for _ in range(n_entries):
            pt = random.choices(price_types, weights=pt_weights, k=1)[0]
            if pt == "our_price":
                price = round(selling * random.uniform(0.90, 1.10), 2)
            elif pt == "cost_price":
                price = round(cost * random.uniform(0.95, 1.05), 2)
            else:
                price = round(selling * random.uniform(0.80, 1.15), 2)
            recorded = datetime.combine(
                random_date(DATE_START, DATE_END), datetime.min.time()
            ) + timedelta(hours=random.randint(0, 23))
            records.append((p["product_id"], Decimal(str(price)), pt, recorded))

    await conn.copy_records_to_table(
        "price_history",
        records=records,
        columns=["product_id", "price_amount", "price_type", "recorded_at"],
        schema_name="inventory",
    )
    print(f"  {len(records)} price history records inserted.")


async def seed_employees(conn) -> list[dict]:
    """Seed 100 employees across 8 departments. Returns employee metadata."""
    print("Seeding employees...")
    employees = []
    total = 100
    used_emails: set[str] = set()

    for dept, frac in DEPARTMENTS.items():
        count = max(1, round(total * frac))
        for _ in range(count):
            name = fake.name()
            # Generate unique email
            base_email = name.lower().replace(" ", ".").replace("..", ".")
            email = f"{base_email}@nasiko.com"
            counter = 1
            while email in used_emails:
                email = f"{base_email}{counter}@nasiko.com"
                counter += 1
            used_emails.add(email)

            phone = fake.phone_number()
            city = random.choice(CITIES)

            # Designation
            desg_name, pay_band, _ = random.choices(
                DESIGNATIONS, weights=[d[2] for d in DESIGNATIONS], k=1
            )[0]
            lo, hi = SALARY_BANDS[pay_band]
            mult = DEPT_SALARY_MULT[dept]
            salary = round(random.randint(lo, hi) * mult, -3)  # round to nearest 1000

            doj = random_date(date(2020, 1, 1), date(2025, 12, 31))
            last_rev = random_date(max(doj, date(2025, 1, 1)), date(2026, 2, 28))

            employees.append({
                "name": name,
                "email": email,
                "phone": phone,
                "department": dept,
                "designation": desg_name,
                "city": city,
                "doj": doj,
                "salary": salary,
                "pay_band": pay_band,
                "last_rev": last_rev,
            })

    records = [
        (
            e["name"], e["email"], e["phone"], e["department"],
            e["designation"], e["city"], e["doj"], True,
            Decimal(str(e["salary"])), "INR", e["pay_band"], e["last_rev"],
        )
        for e in employees
    ]
    await conn.copy_records_to_table(
        "employees",
        records=records,
        columns=[
            "full_name", "email_address", "phone_number", "department",
            "designation", "office_location", "date_of_joining", "is_active",
            "base_salary_amount", "salary_currency", "pay_band",
            "last_salary_revision_date",
        ],
        schema_name="hr",
    )

    # Fetch IDs
    rows = await conn.fetch(
        "SELECT employee_id, email_address FROM hr.employees ORDER BY employee_id"
    )
    id_map = {r["email_address"]: r["employee_id"] for r in rows}
    for e in employees:
        e["employee_id"] = id_map[e["email"]]

    print(f"  {len(employees)} employees inserted.")
    return employees


async def seed_employee_skills(conn, employees: list[dict]) -> dict[int, list[str]]:
    """Seed ~500 employee skills (avg 5 per employee). Returns skills per employee."""
    print("Seeding employee skills...")
    records = []
    emp_skills_map: dict[int, list[str]] = {}

    for e in employees:
        dept = e["department"]
        available = DEPT_SKILLS[dept]
        n_skills = random.randint(3, 8)
        chosen = random.sample(available, min(n_skills, len(available)))
        emp_skills_map[e["employee_id"]] = chosen

        tenure_years = (date(2026, 3, 1) - e["doj"]).days / 365.25
        for skill in chosen:
            yoe = round(min(random.uniform(0.5, tenure_years + 0.5), tenure_years), 1)
            if yoe < 1:
                prof = "beginner"
            elif yoe < 3:
                prof = "intermediate"
            elif yoe < 6:
                prof = "advanced"
            else:
                prof = "expert"
            cat = SKILL_CATEGORIES.get(skill, "domain")
            last_used = random_date(date(2025, 9, 1), date(2026, 3, 1))
            records.append((
                e["employee_id"], skill, cat, prof,
                Decimal(str(yoe)), last_used,
            ))

    await conn.copy_records_to_table(
        "employee_skills",
        records=records,
        columns=[
            "employee_id", "skill_name", "skill_category",
            "proficiency_level", "years_of_experience", "last_used_date",
        ],
        schema_name="hr",
    )
    print(f"  {len(records)} employee skills inserted.")
    return emp_skills_map


async def seed_performance_reviews(
    conn, employees: list[dict], emp_skills: dict[int, list[str]]
):
    """Seed 200 performance reviews (2 per employee)."""
    print("Seeding performance reviews...")
    records = []
    periods = ["2025-H1", "2025-H2"]

    for e in employees:
        skills = emp_skills.get(e["employee_id"], [])
        for period in periods:
            rating = round(float(np.clip(np.random.normal(3.5, 0.7), 1.0, 5.0)), 1)
            reviewer = fake.name()
            text = generate_review_text(
                e["name"], e["department"], period, rating, skills
            )
            records.append((
                e["employee_id"], period, reviewer,
                Decimal(str(rating)), text,
            ))

    await conn.copy_records_to_table(
        "performance_reviews",
        records=records,
        columns=[
            "employee_id", "review_period", "reviewer_name",
            "rating_score", "review_text",
        ],
        schema_name="hr",
    )
    print(f"  {len(records)} performance reviews inserted.")


async def seed_leave_records(conn, employees: list[dict]):
    """Seed ~300 leave records (~3 per employee)."""
    print("Seeding leave records...")
    leave_types = ["casual", "sick", "earned", "work_from_home", "maternity", "paternity", "unpaid"]
    lt_weights = [0.35, 0.20, 0.15, 0.15, 0.05, 0.05, 0.05]
    status_choices = ["approved", "approved", "approved", "approved",
                      "approved", "approved", "approved", "approved",
                      "pending", "rejected", "cancelled"]  # ~80/10/5/5

    duration_map = {
        "casual": (1, 3), "sick": (1, 7), "earned": (5, 15),
        "work_from_home": (1, 2), "maternity": (30, 90),
        "paternity": (5, 15), "unpaid": (1, 10),
    }

    records = []
    for e in employees:
        n_leaves = random.randint(2, 5)
        for _ in range(n_leaves):
            lt = random.choices(leave_types, weights=lt_weights, k=1)[0]
            lo, hi = duration_map[lt]
            duration = random.randint(lo, hi)
            start = random_date(
                max(e["doj"], date(2025, 1, 1)),
                date(2026, 2, 28),
            )
            end = start + timedelta(days=duration)
            status = random.choice(status_choices)
            records.append((e["employee_id"], lt, start, end, status))

    await conn.copy_records_to_table(
        "leave_records",
        records=records,
        columns=["employee_id", "leave_type", "start_date", "end_date", "approval_status"],
        schema_name="hr",
    )
    print(f"  {len(records)} leave records inserted.")


async def seed_sales_transactions(
    conn, products: list[dict], office_ids: list[int], cost_lookup: dict[int, float]
):
    """Seed ~10000 sales transactions with power-law and seasonal patterns."""
    print("Seeding sales transactions...")
    total_days = (DATE_END - DATE_START).days + 1

    # Pre-generate sale days (~10% of total days)
    all_dates = [DATE_START + timedelta(days=i) for i in range(total_days)]
    n_sale_days = max(1, int(total_days * 0.10))
    sale_days = set(random.sample(all_dates, n_sale_days))

    # Power-law product weights
    n = len(products)
    weights = np.random.zipf(a=1.5, size=n).astype(float)
    weights = weights / weights.sum()

    # Build selling price lookup
    pricing_rows = await conn.fetch(
        "SELECT product_id, current_selling_price_per_unit FROM inventory.product_pricing"
    )
    sell_lookup = {r["product_id"]: float(r["current_selling_price_per_unit"]) for r in pricing_rows}

    # Office weights (HQ gets more traffic)
    office_weights = np.array([3.0, 2.0, 2.0, 1.5, 1.5, 1.0, 1.0])
    office_weights = office_weights / office_weights.sum()

    records = []
    target = 10000
    per_day = target / total_days

    for day_offset in range(total_days):
        d = all_dates[day_offset]
        is_sale = d in sale_days
        month = d.month
        weekday = d.weekday()

        # Seasonal + weekend multiplier
        mult = 1.0
        if month == 11:
            mult = 1.5
        elif month == 12:
            mult = 2.0
        elif month == 1:
            mult = 0.8
        if weekday >= 5:  # weekend
            mult *= 1.3
        if is_sale:
            mult *= 1.5

        count = max(1, int(per_day * mult + random.gauss(0, 3)))
        for _ in range(count):
            pid = int(np.random.choice([p["product_id"] for p in products], p=weights))
            oid = int(np.random.choice(office_ids, p=office_weights))
            qty = random.randint(1, 10)
            cost = cost_lookup.get(pid, 100.0)
            sell = sell_lookup.get(pid, cost * 1.3)

            if is_sale:
                sell = round(sell * random.uniform(0.75, 0.90), 2)

            total_sell = round(qty * sell, 2)
            total_cost = round(qty * cost, 2)
            discount = round(total_sell * random.uniform(0.05, 0.20), 2) if is_sale else 0.0
            payment = random.choice(PAYMENT_METHODS)
            customer = fake.name()

            records.append((
                oid, pid, customer, qty,
                Decimal(str(cost)), Decimal(str(sell)),
                Decimal(str(total_sell)), Decimal(str(total_cost)),
                Decimal(str(discount)), payment, is_sale, d,
            ))

    if len(records) > target:
        records = random.sample(records, target)

    # Insert in chunks of 5000
    chunk = 5000
    for i in range(0, len(records), chunk):
        await conn.copy_records_to_table(
            "sales_transactions",
            records=records[i:i + chunk],
            columns=[
                "office_id", "product_id", "customer_name", "quantity_sold",
                "cost_price_per_unit", "selling_price_per_unit",
                "total_selling_amount", "total_cost_amount",
                "discount_amount", "payment_method", "is_sale_day",
                "transaction_date",
            ],
            schema_name="finance",
        )
    print(f"  {len(records)} sales transactions inserted.")


# ---------------------------------------------------------------------------
# Embedding generation (phase 2)
# ---------------------------------------------------------------------------
async def generate_all_embeddings(conn):
    """Generate and update embeddings for all tables that need them."""
    print("\n=== Generating embeddings ===")

    # 1. Products
    print("Embedding products...")
    rows = await conn.fetch(
        "SELECT product_id, product_name, product_description, category, subcategory "
        "FROM inventory.products ORDER BY product_id"
    )
    texts = [
        f"{r['product_name']}. {r['product_description']}. Category: {r['category']}, {r['subcategory']}"
        for r in rows
    ]
    vecs = embed_texts(texts)
    for r, v in zip(rows, vecs):
        await conn.execute(
            "UPDATE inventory.products SET product_name_embedding = $1::vector WHERE product_id = $2",
            vec_to_pg(v), r["product_id"],
        )
    print(f"  {len(rows)} product embeddings updated.")

    # 2. Product pricing
    print("Embedding product pricing...")
    rows = await conn.fetch("""
        SELECT pp.pricing_id, p.product_name,
               pp.cost_price_per_unit, pp.current_selling_price_per_unit,
               pp.margin_percentage, pp.competitor_average_price,
               pp.demand_elasticity_coefficient,
               pp.average_daily_units_normal_day, pp.average_daily_units_sale_day
        FROM inventory.product_pricing pp
        JOIN inventory.products p ON p.product_id = pp.product_id
        ORDER BY pp.pricing_id
    """)
    texts = [
        f"{r['product_name']}. Cost: {r['cost_price_per_unit']}. "
        f"Selling: {r['current_selling_price_per_unit']}. "
        f"Margin: {r['margin_percentage']}%. "
        f"Competitors avg: {r['competitor_average_price']}. "
        f"Elasticity: {r['demand_elasticity_coefficient']}. "
        f"Normal day units: {r['average_daily_units_normal_day']}. "
        f"Sale day units: {r['average_daily_units_sale_day']}."
        for r in rows
    ]
    vecs = embed_texts(texts)
    for r, v in zip(rows, vecs):
        await conn.execute(
            "UPDATE inventory.product_pricing SET pricing_context_embedding = $1::vector WHERE pricing_id = $2",
            vec_to_pg(v), r["pricing_id"],
        )
    print(f"  {len(rows)} pricing embeddings updated.")

    # 3. Employee skills
    print("Embedding employee skills...")
    rows = await conn.fetch(
        "SELECT employee_skill_id, skill_name, skill_category "
        "FROM hr.employee_skills ORDER BY employee_skill_id"
    )
    texts = [f"{r['skill_name']} ({r['skill_category']})" for r in rows]
    vecs = embed_texts(texts)
    for r, v in zip(rows, vecs):
        await conn.execute(
            "UPDATE hr.employee_skills SET skill_name_embedding = $1::vector WHERE employee_skill_id = $2",
            vec_to_pg(v), r["employee_skill_id"],
        )
    print(f"  {len(rows)} skill embeddings updated.")

    # 4. Employees (needs skills aggregated)
    print("Embedding employee profiles...")
    rows = await conn.fetch("""
        SELECT e.employee_id, e.full_name, e.designation, e.department, e.office_location,
               COALESCE(string_agg(es.skill_name, ', '), '') AS skills
        FROM hr.employees e
        LEFT JOIN hr.employee_skills es ON es.employee_id = e.employee_id
        GROUP BY e.employee_id, e.full_name, e.designation, e.department, e.office_location
        ORDER BY e.employee_id
    """)
    texts = [
        f"{r['full_name']}, {r['designation']} in {r['department']} department "
        f"at {r['office_location']}. Skills: {r['skills']}"
        for r in rows
    ]
    vecs = embed_texts(texts)
    for r, v in zip(rows, vecs):
        await conn.execute(
            "UPDATE hr.employees SET employee_profile_embedding = $1::vector WHERE employee_id = $2",
            vec_to_pg(v), r["employee_id"],
        )
    print(f"  {len(rows)} employee profile embeddings updated.")

    # 5. Performance reviews
    print("Embedding performance reviews...")
    rows = await conn.fetch(
        "SELECT review_id, review_text FROM hr.performance_reviews ORDER BY review_id"
    )
    texts = [r["review_text"] for r in rows]
    vecs = embed_texts(texts)
    for r, v in zip(rows, vecs):
        await conn.execute(
            "UPDATE hr.performance_reviews SET review_text_embedding = $1::vector WHERE review_id = $2",
            vec_to_pg(v), r["review_id"],
        )
    print(f"  {len(rows)} review embeddings updated.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    print(f"Connecting to: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'local'}...")
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        # Idempotency check — skip if data already exists
        count = await conn.fetchval("SELECT COUNT(*) FROM inventory.products")
        if count > 0:
            print(f"Data already seeded ({count} products found). Skipping.")
            return

        # Seed in dependency order
        products = await seed_products(conn)
        warehouse_ids = await seed_warehouses(conn)
        office_ids = await seed_offices(conn)
        await seed_inventory_levels(conn, products, warehouse_ids)
        cost_lookup = await seed_stock_movements(conn, products, warehouse_ids)
        await seed_product_pricing(conn, products, cost_lookup)
        await seed_price_history(conn, products, cost_lookup)
        employees = await seed_employees(conn)
        emp_skills = await seed_employee_skills(conn, employees)
        await seed_performance_reviews(conn, employees, emp_skills)
        await seed_leave_records(conn, employees)
        await seed_sales_transactions(conn, products, office_ids, cost_lookup)

        # Refresh materialized views
        print("\nRefreshing materialized views...")
        await conn.execute("REFRESH MATERIALIZED VIEW finance.mv_daily_office_profit_loss")
        await conn.execute("REFRESH MATERIALIZED VIEW finance.mv_daily_product_revenue")
        print("  Materialized views refreshed.")

        # Generate embeddings
        await generate_all_embeddings(conn)

        # Final summary
        print("\n=== Summary ===")
        tables = [
            ("inventory.products", "product_id"),
            ("inventory.warehouses", "warehouse_id"),
            ("inventory.inventory_levels", "product_id"),
            ("inventory.stock_movements", "movement_id"),
            ("inventory.product_pricing", "pricing_id"),
            ("inventory.price_history", "history_id"),
            ("hr.employees", "employee_id"),
            ("hr.employee_skills", "employee_skill_id"),
            ("hr.performance_reviews", "review_id"),
            ("hr.leave_records", "leave_record_id"),
            ("finance.offices", "office_id"),
            ("finance.sales_transactions", "transaction_id"),
        ]
        for table, _ in tables:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            print(f"  {table}: {count} rows")

        mv_count1 = await conn.fetchval("SELECT COUNT(*) FROM finance.mv_daily_office_profit_loss")
        mv_count2 = await conn.fetchval("SELECT COUNT(*) FROM finance.mv_daily_product_revenue")
        print(f"  finance.mv_daily_office_profit_loss: {mv_count1} rows")
        print(f"  finance.mv_daily_product_revenue: {mv_count2} rows")

        print("\nSeeding complete!")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
