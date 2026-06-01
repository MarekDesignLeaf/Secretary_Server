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

st, body = call("POST","/auth/login", body={"email":"aaa@bbb.com","password":"123456789aaa"})
print(f"LOGIN aaa@bbb.com / 123456789aaa: {st}")
if st == 200:
    j = json.loads(body)
    print("  OK role:", j.get("role"), "company_id:", j.get("company_id"))
    with open(r"C:\Users\hutra\AndroidStudioProjects\secretary\server\.rv_token","w") as f:
        f.write(j["access_token"])
    with open(r"C:\Users\hutra\AndroidStudioProjects\secretary\server\.rv_company","w") as f:
        f.write(j.get("company_id",""))
else:
    print("  ", body[:200])
