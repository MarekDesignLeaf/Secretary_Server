import urllib.request, json

BASE = "https://web-production-4b451.up.railway.app/api/v1"
TOKEN = open(r"C:\Users\hutra\AndroidStudioProjects\secretary\server\.rv_token").read().strip()
OUT = []
def log(s=""):
    print(s); OUT.append(s)

def call(method, path, body=None):
    headers={"Content-Type":"application/json","Authorization":f"Bearer {TOKEN}"}
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(BASE+path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except: return e.code, e.read().decode()
    except Exception as e:
        return "ERR", str(e)

def voice(utt, confirmed=False):
    return call("POST","/voice/execute", {"utterance":utt,"confirmed":confirmed})

def report(n, title, utt, confirmed=True, db_path=None, db_label=None):
    log("\n" + "#"*70); log(f"# {n}. {title}"); log("#"*70)
    log(f"USER UTTERANCE: {utt}")
    st, res = voice(utt, confirmed=confirmed)
    log(f"ENDPOINT: POST /voice/execute  (confirmed={confirmed})")
    log(f"HTTP {st}")
    log(f"RESOLVED INTENT: {res.get('resolved_intent')}")
    log(f"EXECUTED: {res.get('executed')}")
    log(f"ENTITY_ID: {res.get('entity_id')}")
    log(f"MESSAGE (to user): {res.get('message')}")
    log(f"DATA: {json.dumps(res.get('data',{}))[:300]}")
    if db_path:
        st2, data = call("GET", db_path)
        n2 = len(data) if isinstance(data,list) else "?"
        log(f"DB VERIFY GET {db_path}: {st2} count={n2}")
        if isinstance(data,list) and data:
            log(f"  LATEST: {json.dumps(data[-1])[:250]}")
    return res

# 3. Move it to 11 (we have NO meeting since #2 failed; test the phrase anyway)
report(3, "Presun ji na 11", "Přesuň ji na 11", confirmed=True, db_path="/calendar/events")

# 4. Cancel it
report(4, "Zrus ji", "Zruš ji", confirmed=True, db_path="/calendar/events")

# 5. Create client John Smith
report(5, "Vytvor klienta John Smith", "Vytvoř klienta John Smith", confirmed=True, db_path="/crm/clients")

# 6. Create task for Daniel
report(6, "Vytvor ukol pro Daniela", "Vytvoř úkol pro Daniela", confirmed=True, db_path="/crm/tasks")

# 7. Create work report
report(7, "Vytvor work report", "Vytvoř work report", confirmed=True, db_path="/work-reports")

with open(r"C:\Users\hutra\AndroidStudioProjects\secretary\server\_rv_p2.txt","w",encoding="utf-8") as f:
    f.write("\n".join(OUT))
print("\n[part 3-7 done]")
