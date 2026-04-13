cd C:\Users\hutra\AndroidStudioProjects\secretary\server
$f = [System.IO.File]::ReadAllText("$PWD\main.py")

# === BLOCKER 1: Fix GPT create_job - lookup client_id from client_name ===
$old1 = @'
            if action == "CREATE_JOB":
                t = args.get("title","Zakazka")
                conn = get_db_conn()
                try:
                    code = f"JOB-{uuid.uuid4().hex[:6].upper()}"
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO jobs (job_number,job_title,job_status,start_date_planned) VALUES (%s,%s,'nova',%s) RETURNING id",
                            (code,t,args.get("start_date")))
'@
$new1 = @'
            if action == "CREATE_JOB":
                t = args.get("title","Zakazka")
                conn = get_db_conn()
                try:
                    code = f"JOB-{uuid.uuid4().hex[:6].upper()}"
                    cname = args.get("client_name","")
                    cid = None
                    with conn.cursor() as cur:
                        if cname:
                            cur.execute("SELECT id FROM clients WHERE display_name ILIKE %s AND deleted_at IS NULL LIMIT 1",(f"%{cname}%",))
                            row = cur.fetchone()
                            if row: cid = row['id']
                        cur.execute("INSERT INTO jobs (job_number,client_id,job_title,job_status,start_date_planned) VALUES (%s,%s,%s,'nova',%s) RETURNING id",
                            (code,cid,t,args.get("start_date")))
'@
$f = $f.Replace($old1, $new1)

# === BLOCKER 2: Add seed user endpoint ===
$seedEndpoint = @'

@app.post("/debug/seed-admin")
async def seed_admin():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO roles (role_name,description) VALUES ('admin','Full access') ON CONFLICT (role_name) DO NOTHING")
            cur.execute("SELECT id FROM roles WHERE role_name='admin'")
            rid = cur.fetchone()['id']
            cur.execute("""INSERT INTO users (tenant_id,role_id,first_name,last_name,display_name,email,phone,status,password_hash)
                VALUES (1,%s,'Marek','Sima','Marek Sima','marek@designleaf.co.uk','+44 7XXX','active','not_set')
                ON CONFLICT (email) DO UPDATE SET display_name='Marek Sima',role_id=%s RETURNING id,display_name,email""",(rid,rid))
            user = dict(cur.fetchone())
            conn.commit()
            return {"status":"ok","user":user}
    except Exception as e:
        conn.rollback()
        return {"status":"error","message":str(e)}
    finally: release_conn(conn)

'@
$healthMarker = '@app.get("/health")'
$f = $f.Replace($healthMarker, $seedEndpoint + $healthMarker)

# === BLOCKER 3: Expand POST /crm/clients to accept all fields ===
$old3 = @'
        code = f"CL-{uuid.uuid4().hex[:6].upper()}"
        with conn.cursor() as cur:
            cur.execute("INSERT INTO clients (client_code,client_type,display_name,email_primary,phone_primary,status) VALUES (%s,%s,%s,%s,%s,'active') RETURNING id",
                (code,data.get("type","domestic"),data.get("name",""),data.get("email"),data.get("phone")))
            cid = cur.fetchone()['id']
            log_activity(conn,"client",cid,"create",f"Klient {data.get('name','')} vytvoren")
            conn.commit()
        return {"id":cid,"client_code":code,"status":"success"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)
'@
$new3 = @'
        code = f"CL-{uuid.uuid4().hex[:6].upper()}"
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO clients (client_code,client_type,title,first_name,last_name,display_name,
                company_name,company_registration_no,vat_no,email_primary,email_secondary,
                phone_primary,phone_secondary,website,preferred_contact_method,
                billing_address_line1,billing_city,billing_postcode,billing_country,
                status,is_commercial,tenant_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,1) RETURNING id""",
                (code,data.get("type",data.get("client_type","domestic")),
                 data.get("title"),data.get("first_name"),data.get("last_name"),
                 data.get("name",data.get("display_name","")),
                 data.get("company_name"),data.get("company_registration_no"),data.get("vat_no"),
                 data.get("email",data.get("email_primary")),data.get("email_secondary"),
                 data.get("phone",data.get("phone_primary")),data.get("phone_secondary"),
                 data.get("website"),data.get("preferred_contact_method","email"),
                 data.get("billing_address_line1"),data.get("billing_city"),
                 data.get("billing_postcode"),data.get("billing_country","GB"),
                 data.get("is_commercial",False)))
            cid = cur.fetchone()['id']
            log_activity(conn,"client",cid,"create",f"Klient {data.get('name',data.get('display_name',''))} vytvoren")
            conn.commit()
        return {"id":cid,"client_code":code,"status":"success"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)
'@
$f = $f.Replace($old3, $new3)

[System.IO.File]::WriteAllText("$PWD\main.py", $f)
git add main.py
git commit -m "Fix 3 blockers: create_job client_id, seed-admin, full client create"
git push
Write-Host "HOTOVO. Pockej 2min na deploy." -ForegroundColor Green
