"""
uninstall.py  –  sendgrid_email v1.0.0
"""

TOOL_ID = "sendgrid_email"


def uninstall(conn, tenant_id: int, purge_secrets: bool = False):
    """
    Deactivate tool config. Set purge_secrets=True to also remove secrets.
    conn        – psycopg2 connection (already in transaction)
    tenant_id   – target tenant
    """
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE crm.tenant_tool_config
               SET is_active = FALSE, updated_at = NOW()
             WHERE tenant_id = %s AND tool_id = %s
        """, (tenant_id, TOOL_ID))
        if purge_secrets:
            cur.execute("""
                UPDATE crm.tenant_tool_secret
                   SET is_active = FALSE, updated_at = NOW()
                 WHERE tenant_id = %s AND tool_id = %s
            """, (tenant_id, TOOL_ID))
        cur.execute("""
            UPDATE crm.tool_registry
               SET install_status = 'uninstalled', updated_at = NOW()
             WHERE tenant_id = %s AND tool_id = %s
        """, (tenant_id, TOOL_ID))
