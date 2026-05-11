"""
uninstall.py — nature_recognition v1.0.0
Removes Nature Recognition from a tenant.
The tool_installer.py handles deactivating hub_tiles and config slots automatically.
"""

TOOL_ID = "nature_recognition"


def uninstall(conn, tenant_id: int, purge_secrets: bool = False):
    """Called for custom cleanup. Core uninstall handled by tool_installer."""
    pass  # hub_tiles and slots deactivated by tool_installer._remove_hub_tiles
