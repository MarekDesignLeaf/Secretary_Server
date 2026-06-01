import urllib.request, json

BASE = "https://web-production-4b451.up.railway.app/api/v1"

def call(method, path, token=None, body=None):
    headers={"Content-Type":"application/json"}
    if token: headers["Authorization"]=f"Bearer {token}"
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(BASE+path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return "ERR", str(e)

# bootstrap status
print("bootstrap/status:", call("GET","/bootstrap/status"))
# first-login-users needs auth, but let's see the exact error
print("first-login-users (no auth):", call("GET","/auth/first-login-users"))

# Try a few email/password combos that might be the real seed
combos = [
    ("marek@designleaf.co.uk","123456789aaa"),
    ("marek@designleaf.co.uk","123456789Aaa"),
    ("admin@designleaf.co.uk","123456789aaa"),
    ("marek@designleaf.cz","123456789aaa"),
]
for email,pw in combos:
    st,_ = call("POST","/auth/login", body={"email":email,"password":pw})
    print(f"login {email} / {pw}: {st}")
