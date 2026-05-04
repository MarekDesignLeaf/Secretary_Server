#!/usr/bin/env python3
"""One-time script: list users and restore Daniel if missing."""
import os, sys, bcrypt
from urllib.parse import urlparse
import psycopg2
from psycopg2.extras import RealDictCursor

db_url = os.getenv("DATABASE_URL", "")
if not db_url:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)

p = urlparse(db_url)
conn = psycopg2.connect(
    dbname=p.path.lstrip("/"),
    user=p.username,
    password=p.password,
    host=p.hostname,
    port=p.port or 5432,
    cursor_factory=RealDictCursor,
    options="-c search_path=crm,public"
)
conn.autocommit = True

with conn.cursor() as cur:
    print("\n=== ALL USERS IN crm.users ===")
    cur.execute("""
        SELECT id, email, display_name, role, is_active, deleted_at, tenant_id
        FROM crm.users ORDER BY id
    """)
    users = cur.fetchall()
    for u in users:
        print(f"  id={u['id']} email={u['email']} name={u['display_name']} role={u['role']} active={u['is_active']} deleted={u['deleted_at']}")

    print(f"\nTotal users: {len(users)}")

    # Check if Daniel exists
    cur.execute("SELECT id FROM crm.users WHERE display_name ILIKE '%daniel%' OR email ILIKE '%daniel%'")
    daniel = cur.fetchone()

    if daniel:
        print(f"\nDaniel FOUND: id={daniel['id']}")
    else:
        print("\nDaniel NOT FOUND — creating...")
        # Get tenant_id from existing users
        cur.execute("SELECT tenant_id FROM crm.users WHERE is_active=true AND deleted_at IS NULL LIMIT 1")
        tenant_row = cur.fetchone()
        tenant_id = tenant_row["tenant_id"] if tenant_row else 1

        # Hash password
        pw_hash = bcrypt.hashpw("Daniel2026!".encode(), bcrypt.gensalt()).decode()

        cur.execute("""
            INSERT INTO crm.users (email, display_name, role, password_hash, is_active, tenant_id, first_login)
            VALUES (%s, %s, %s, %s, true, %s, false)
            ON CONFLICT (email) DO UPDATE SET
                deleted_at = NULL, is_active = true,
                display_name = EXCLUDED.display_name
            RETURNING id
        """, ("daniel@designleaf.co.uk", "Daniel", "worker", pw_hash, tenant_id))
        new_user = cur.fetchone()
        print(f"Daniel CREATED: id={new_user['id']}, email=daniel@designleaf.co.uk, password=Daniel2026!")

    print("\n=== DONE ===")
conn.close()
