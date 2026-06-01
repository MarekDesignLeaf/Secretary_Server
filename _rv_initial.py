import urllib.request, json

BASE = "https://web-production-4b451.up.railway.app/api/v1"
TOKEN = open(r"C:\Users\hutra\AndroidStudioProjects\secretary\server\.rv_token").read().strip()

def call(method, path, body=None):
    headers={"Content-Type":"application/json","Authorization":f"Bearer {TOKEN}"}
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(BASE+path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return "ERR", str(e)

print("=== INITIAL DB STATE ===")
for label, path in [("calendar","/calendar/events"),("clients","/crm/clients"),
                    ("tasks","/crm/tasks"),("work_reports","/work-reports")]:
    st, data = call("GET", path)
    n = len(data) if isinstance(data, list) else "?"
    print(f"{label}: {st} count={n}")
