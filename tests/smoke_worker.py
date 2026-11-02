#!/usr/bin/env python3
"""Exercise free local Worker+SQLite Durable Object before publishing."""
import json
import subprocess
import time
import urllib.error
import urllib.request

URL="http://127.0.0.1:8793"
CLIENT="12345678-1234-4234-8234-123456789012"
ARTICLE="apology-before-incident"

def req(method,path,payload=None):
    headers={"Origin":URL,"Content-Type":"application/json"}
    data=json.dumps(payload).encode() if payload else None
    with urllib.request.urlopen(urllib.request.Request(
        URL+path,data=data,headers=headers,method=method
    ),timeout=5) as r:
        return r.read().decode()

def main():
    proc=subprocess.Popen(
      ["npx","--yes","wrangler@4","dev","--local","--port","8793"],
      stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True
    )
    try:
        for _ in range(70):
            if proc.poll() is not None:
                raise RuntimeError("Worker exited before becoming ready")
            try:
                text=req("GET","/api/engagement?articles="+ARTICLE)
                j=json.loads(text)
                if j.get("unit")=="anonymous-browser":
                    break
            except (urllib.error.URLError,TimeoutError,json.JSONDecodeError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Worker did not respond within 70 seconds")
        first=json.loads(req("POST","/api/engagement",{
            "action":"visit","article":ARTICLE,"clientId":CLIENT
        }))
        again=json.loads(req("POST","/api/engagement",{
            "action":"visit","article":ARTICLE,"clientId":CLIENT
        }))
        assert first["visitors"]==again["visitors"],(first,again)
        assert first["readers"]==again["readers"],(first,again)
        a=json.loads(req("POST","/api/engagement",{
            "action":"like","article":ARTICLE,"clientId":CLIENT
        }))
        b=json.loads(req("POST","/api/engagement",{
            "action":"like","article":ARTICLE,"clientId":CLIENT
        }))
        assert a["liked"] is True and b["liked"] is False,(a,b)
        assert a["likes"]==b["likes"]+1,(a,b)
        home=req("GET","/")
        assert "未発生新聞" in home
        assert "data-future-clock" in home
        print("Local engagement smoke test passed: visit dedup, like toggles, static pages")
    finally:
        proc.terminate()
        try:proc.communicate(timeout=6)
        except subprocess.TimeoutExpired:proc.kill();proc.communicate()

if __name__=="__main__":
    main()
