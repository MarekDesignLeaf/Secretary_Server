"""
install.py  –  sendgrid_email v1.0.0
SendGrid Email tool installer for Secretary CRM.
Auto-run via: POST /tools/install (multipart/form-data, file=sendgrid_email_v1_0_0_empty_slots.zip)
"""

TOOL_ID   = "sendgrid_email"
TOOL_NAME = "SendGrid Email"
VERSION   = "1.0.0"

# Secret slots: values collected interactively — NEVER stored in this file
REQUIRED_SECRET_SLOTS = [
    {
        "slot_name": "api_key",
        "display_name": "SendGrid API Key",
        "required": True,
        "validation_type": "text"
    }
]

# Config slots (non-secret)
REQUIRED_CONFIG_SLOTS = [
    {
        "slot_name": "from_email",
        "display_name": "From Email Address",
        "required": True,
        "validation_type": "email"
    },
    {
        "slot_name": "from_name",
        "display_name": "From Display Name",
        "required": False,
        "validation_type": "text"
    }
]


def install(conn, tenant_id: int, slot_values: dict):
    """
    Called by tool_installer after slot values are collected.
    conn        – psycopg2 connection (already in transaction)
    tenant_id   – target tenant
    slot_values – {slot_name: plain_value} for ALL required slots
    """
    from tool_secret_store import save_secret, save_config
    for slot in REQUIRED_SECRET_SLOTS:
        name = slot["slot_name"]
        if name in slot_values:
            save_secret(conn, tenant_id, TOOL_ID, name, slot_values[name])
    for slot in REQUIRED_CONFIG_SLOTS:
        name = slot["slot_name"]
        if name in slot_values:
            save_config(conn, tenant_id, TOOL_ID, name, value_text=slot_values[name])


def verify(conn, tenant_id: int) -> bool:
    """Return True if all required secret slots are present in DB."""
    from tool_secret_store import load_secret
    for slot in REQUIRED_SECRET_SLOTS:
        if slot.get("required", True):
            val = load_secret(conn, tenant_id, TOOL_ID, slot["slot_name"])
            if not val:
                return False
    return True
