#!/usr/bin/env python3
"""Smoke-test only a *local* Cloudflare worker; never mutate production counters."""
import json
from ipaddress import ip_address
import sys
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import urlsplit

url = sys.argv[1].rstrip("/") if len(sys.argv)>1 else "http://127.0.0.1:8788"
target = urlsplit(url)
try:
    loopback = target.hostname == "localhost" or ip_address(target.hostname or "").is_loopback
except ValueError:
    loopback = False
if (target.scheme not in ("http", "https") or not loopback or target.username or target.password
        or target.path or target.query or target.fragment):
    raise SystemExit("Refusing to mutate non-local counters: use a loopback Worker URL only")

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
check("pageViews" in baseline and "pageViews" in m,
      "PV must be separate from legacy anonymous-browser counters")
check(isinstance(baseline.get("pageViewTracking"),dict),"PV tracking metadata must exist")
initial_pv=baseline["pageViews"] or 0
initial_article_pv=m["pageViews"] or 0
client=str(uuid.uuid4())
record={"action":"visit","article":"apology-before-incident","clientId":client,"channel":"x",
        "viewId":str(uuid.uuid4())}
for i in range(2):
    code,_,data=req("POST","/api/engagement",record)
    check(code==200,"visit API status")
counts=json.loads(data)
check(counts["visitors"]==v+1,"visit must count one browser only")
check(counts["readers"]==m["readers"]+1,"article reads must dedupe")
check(counts["pageViews"]==initial_pv+1,"retrying one display must add only one site PV")
check(counts["articlePageViews"]==initial_article_pv+1,"retrying one display must add only one article PV")
check(counts["pageViewTracking"].get("startedAtUtc"),"first display establishes measurement start")
check(counts["today"].get("pageViewsCoverage")=="in-progress","today must remain an incomplete day")
code,_,snapshot=req("GET","/api/engagement?articles=apology-before-incident")
check(code==200,"updated audience status")
snapshot=json.loads(snapshot)
check(snapshot["channels"]["x"]==initial_x+1,"external X visitors dedupe per browser")
check(snapshot["pageViews"]==initial_pv+1,"reading a counter must never add a PV")
check(snapshot["articles"]["apology-before-incident"]["pageViews"]==initial_article_pv+1,
      "article GET exposes separate PV")
record["viewId"]=str(uuid.uuid4())
code,_,data=req("POST","/api/engagement",record)
reload=json.loads(data)
check(reload["pageViews"]==initial_pv+2 and reload["articlePageViews"]==initial_article_pv+2,
      "a real reload with a fresh display ID adds another PV")
check(reload["visitors"]==v+1 and reload["readers"]==m["readers"]+1,
      "a real reload must not inflate distinct browsers")
code,_,data=req("POST","/api/engagement",
                {"action":"visit","clientId":client,"viewId":str(uuid.uuid4())})
check(json.loads(data)["pageViews"]==initial_pv+3,"home page display counts in site-wide PV")
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
summary=json.loads(data)
published=summary["articles"]["apology-before-incident"]
check(published["votes"]["plausible"]==first["votes"]["plausible"],"GET counts include vote")
check(summary["pageViews"]==initial_pv+3 and published["pageViews"]==initial_article_pv+2,
      "likes, votes and subsequent GETs never add PV")
page_code,_,page=req("GET","/articles/apology-before-incident/")
check(page_code==200 and b'data-future-poll' in page and b'data-poll-choice="plausible"' in page,
      "article page must include voting controls")
code,_,body=req("GET","/")
check(code==200 and b"<!doctype html>" in body,"static site fallback")
print("PASS: real site/article PV, retry dedupe, browser dedupe, X attribution, likes, votes, static assets")

