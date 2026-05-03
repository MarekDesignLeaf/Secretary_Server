"""
install.py — nature_recognition v1.0.0
Nature Recognition tool installer for Secretary CRM.
Install via: POST /tools/install (multipart/form-data, file=nature_recognition_v1_0_0.zip)

After install, three tiles appear in ToolsHubScreen:
  • Plant identification  (tile_key: identify)
  • Plant health check    (tile_key: health)
  • Mushroom identification (tile_key: mushroom)

The installer reads hub_tiles.json and registers them in crm.tool_hub_tiles.
Required secret slot: openai_api_key (GPT-4o vision access)
"""

TOOL_ID   = "nature_recognition"
TOOL_NAME = "Nature Recognition"
VERSION   = "1.0.0"

REQUIRED_SECRET_SLOTS = [
    {
        "slot_name": "openai_api_key",
        "display_name": "OpenAI API Key",
        "required": True,
        "validation_type": "text"
    }
]

REQUIRED_CONFIG_SLOTS = [
    {
        "slot_name": "model",
        "display_name": "Vision Model",
        "required": False,
        "default_value": "gpt-4o",
        "validation_type": "text"
    },
    {
        "slot_name": "max_image_tokens",
        "display_name": "Max Image Tokens",
        "required": False,
        "default_value": "800",
        "validation_type": "integer"
    }
]


def install(conn, tenant_id: int, slot_values: dict):
    """Called by tool_installer after slot values are collected."""
    from tool_secret_store import save_secret, save_config
    for slot in REQUIRED_SECRET_SLOTS:
        name = slot["slot_name"]
        if name in slot_values:
            save_secret(conn, tenant_id, TOOL_ID, name, slot_values[name])
    for slot in REQUIRED_CONFIG_SLOTS:
        name = slot["slot_name"]
        val = slot_values.get(name, slot.get("default_value"))
        if val is not None:
            save_config(conn, tenant_id, TOOL_ID, name, value_text=str(val))


def verify(conn, tenant_id: int) -> bool:
    """Return True if all required secret slots are present."""
    from tool_secret_store import load_secret
    for slot in REQUIRED_SECRET_SLOTS:
        if slot.get("required", True):
            if not load_secret(conn, tenant_id, TOOL_ID, slot["slot_name"]):
                return False
    return True
