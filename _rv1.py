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

def section(n, title):
    log("\n" + "#"*70)
    log(f"# {n}. {title}")
    log("#"*70)

# ============ 1. Co mám zítra v kalendáři? ============
section(1, "Co mam zitra v kalendari?")
utt = "Co mám zítra v kalendáři?"
log(f"USER UTTERANCE: {utt}")
st, res = voice(utt)
log(f"ENDPOINT CALLED: POST /voice/execute")
log(f"REQUEST: {{'utterance': '{utt}', 'confirmed': false}}")
log(f"HTTP {st}")
log(f"RESOLVED INTENT: {res.get('resolved_intent')}")
log(f"ACTION: {res.get('action')}")
log(f"EXECUTED: {res.get('executed')}")
log(f"MESSAGE (shown to user): {res.get('message')}")
log(f"DATA: {json.dumps(res.get('data',{}))[:300]}")

# ============ 2. Přidej schůzku zítra v 10 ============
section(2, "Pridej schuzku zitra v 10")
utt = "Přidej schůzku zítra v 10"
log(f"USER UTTERANCE: {utt}")
st, res = voice(utt, confirmed=False)
log(f"--- First call WITHOUT confirm ---")
log(f"HTTP {st} intent={res.get('resolved_intent')} executed={res.get('executed')} needs_confirm={res.get('requires_confirmation')}")
log(f"MESSAGE: {res.get('message')}")
log(f"--- Second call WITH confirm=true ---")
st, res = voice(utt, confirmed=True)
log(f"ENDPOINT CALLED: POST /voice/execute -> POST /calendar/events")
log(f"REQUEST: {{'utterance': '{utt}', 'confirmed': true}}")
log(f"HTTP {st}")
log(f"RESOLVED INTENT: {res.get('resolved_intent')}")
log(f"EXECUTED: {res.get('executed')}")
log(f"ENTITY_ID: {res.get('entity_id')}")
log(f"MESSAGE (shown to user): {res.get('message')}")
log(f"RESPONSE DATA: {json.dumps(res.get('data',{}))[:400]}")
meeting_id = res.get("entity_id")

# DB verify
st, events = call("GET","/calendar/events")
log(f"DB VERIFY GET /calendar/events: {st} count={len(events)}")
if events:
    log(f"  STORED IN DB: {json.dumps(events[0])[:350]}")

with open(r"C:\Users\hutra\AndroidStudioProjects\secretary\server\_rv_p1.txt","w",encoding="utf-8") as f:
    f.write("\n".join(OUT))
# stash meeting_id
with open(r"C:\Users\hutra\AndroidStudioProjects\secretary\server\.rv_meeting","w") as f:
    f.write(meeting_id or "")
print("\n[part 1-2 done]")
