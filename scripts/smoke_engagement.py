#!/usr/bin/env python3
"""Smoke-test only a *local* Cloudflare worker; never mutate production counters."""
import json
import sys
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import URLError

url = sys.argv[1].rstrip("/") if len(sys.argv)>1 else "http://127.0.0.1:8788"
def req(method,path,data=None,origin=True):
    headers={"Content-Type":"application/json"}
    if origin:
        headers["Origin"]=url
    data=json.dumps(data).encode() if data is not None else None
    with urlopen(Request(url+path,data=data,method=method,headers=headers),timeout=9) as r:
        return r.status,r.headers.get("content-type"),r.read()

for i in range(40):
    try:
        code,content_type,data=req("GET","/api/engagement?articles=apology-before-incident")
        if code==200:break
    except (OSError,URLError):
        pass
    time.sleep(1)
else:
    raise SystemExit("Worker API never became ready")

def check(condition,msg):
    if not condition:
        raise AssertionError(msg)

check("application/json" in content_type,"JSON content type")
baseline=json.loads(data)
check(baseline.get("unit")=="anonymous-browser","expected browser-scope counts")
check(isinstance(baseline.get("visitors"),int),"visitors integer")
check("apology-before-incident" in baseline.get("articles",{}),"article metrics exist")
channels=baseline.get("channels",{})
check(isinstance(channels,dict) and all(k in channels for k in ("x","search","external","unattributed")),
      "API must expose only coarse attribution categories")
initial_x=channels["x"]
v=baseline["visitors"]
m=baseline["articles"]["apology-before-incident"]
client=str(uuid.uuid4())
record={"action":"visit","article":"apology-before-incident","clientId":client,"channel":"x"}
for i in range(2):
    code,_,data=req("POST","/api/engagement",record)
    check(code==200,"visit API status")
counts=json.loads(data)
check(counts["visitors"]==v+1,"visit must count one browser only")
check(counts["readers"]==m["readers"]+1,"article reads must dedupe")
code,_,snapshot=req("GET","/api/engagement?articles=apology-before-incident")
check(code==200,"updated audience status")
check(json.loads(snapshot)["channels"]["x"]==initial_x+1,"external X visitors dedupe per browser")
like={"action":"like","article":"apology-before-incident","clientId":client}
code,_,data=req("POST","/api/engagement",like)
check(json.loads(data)["likes"]==m["likes"]+1,"liked +1")
code,_,data=req("POST","/api/engagement",like)
check(json.loads(data)["likes"]==m["likes"],"unliked back to baseline")
# Free local Worker: the same browser can vote only once per story.
votes=m.get("votes")
check(isinstance(votes,dict) and all(isinstance(votes.get(k),int) for k in ("plausible","unlikely")),
      "baseline must expose real prediction counts")
vote={"action":"vote","article":"apology-before-incident","clientId":client,"choice":"plausible"}
code,_,data=req("POST","/api/engagement",vote)
first=json.loads(data)
check(code==200 and first.get("vote")=="plausible" and first.get("alreadyVoted") is False,
      "first vote should succeed")
check(first["votes"]["plausible"]==votes["plausible"]+1,"one genuine plausible prediction")
code,_,data=req("POST","/api/engagement",vote)
again=json.loads(data)
check(again.get("alreadyVoted") is True and again["votes"]["plausible"]==first["votes"]["plausible"],
      "retry must not create an extra vote")
vote["choice"]="unlikely"
code,_,data=req("POST","/api/engagement",vote)
unchanged=json.loads(data)
check(unchanged.get("vote")=="plausible" and unchanged["votes"]["unlikely"]==votes["unlikely"],
      "later choice cannot alter the first immutable vote")
code,_,data=req("GET","/api/engagement?articles=apology-before-incident")
published=json.loads(data)["articles"]["apology-before-incident"]
check(published["votes"]["plausible"]==first["votes"]["plausible"],"GET counts include vote")
page_code,_,page=req("GET","/articles/apology-before-incident/")
check(page_code==200 and b'data-future-poll' in page and b'data-poll-choice="plausible"' in page,
      "article page must include voting controls")
code,_,body=req("GET","/")
check(code==200 and b"<!doctype html>" in body,"static site fallback")
print("PASS: browser dedupe, X attribution, likes, real immutable future votes, static assets")
