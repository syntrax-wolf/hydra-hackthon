import json
import logging
from core.db import execute_write

log = logging.getLogger("onboarding.provisioner")

DEPARTMENT_SYSTEMS = {
    "engineering": ["email", "slack", "github", "jira", "confluence"],
    "data_science": ["email", "slack", "github", "jira", "jupyter"],
    "design": ["email", "slack", "figma", "jira", "confluence"],
    "marketing": ["email", "slack", "hubspot", "canva", "analytics"],
    "sales": ["email", "slack", "hubspot", "crm", "analytics"],
    "finance_ops": ["email", "slack", "erp", "jira"],
    "hr_admin": ["email", "slack", "hrms", "jira"],
    "product": ["email", "slack", "jira", "confluence", "figma"],
}


def _generate_account_id(system: str, first: str, last: str) -> str:
    base = f"{first}.{last}"
    if system == "email":
        return f"{base}@horizon.com"
    if system == "slack":
        return f"@{base}"
    if system == "github":
        return f"github.com/{first}-{last}"
    return f"{base}@{system}.horizon.com"


def provision_accounts(onboarding_id: int, employee_name: str, employee_email: str, department: str) -> list[dict]:
    """Provision system accounts for a new employee. Returns list of {system, account_id}."""
    parts = employee_name.strip().split()
    first = parts[0].lower()
    last = parts[-1].lower() if len(parts) > 1 else first

    systems = DEPARTMENT_SYSTEMS.get(department, ["email", "slack", "jira"])
    accounts = []

    for sys_name in systems:
        acct_id = _generate_account_id(sys_name, first, last)
        execute_write(
            "INSERT INTO onboarding.system_accounts (onboarding_id, system_name, account_identifier) VALUES (%s, %s, %s)",
            [onboarding_id, sys_name, acct_id],
        )
        accounts.append({"system": sys_name, "account_id": acct_id})
        log.info("[PROVISION] %s -> %s = %s", employee_name, sys_name, acct_id)

    # Update onboarding record
    execute_write(
        "UPDATE onboarding.onboarding_records SET status = 'provisioned', current_step = 1, accounts_provisioned = %s WHERE onboarding_id = %s",
        [json.dumps(accounts), onboarding_id],
    )
    log.info("[PROVISION] Provisioned %d accounts for onboarding_id=%d", len(accounts), onboarding_id)
    return accounts
