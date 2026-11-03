#!/usr/bin/env python3
"""Buffer Free delivery status check; never asserts X impressions or live reach.

Uses only the account's existing free Buffer access. A Buffer 'sent' state
is a platform-side delivery indication, NOT proof of X post visibility.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
from publish import choose_x_channel, list_existing  # noqa: E402


def report(state, remote):
    by_buffer={p.get("id"):p.get("status") for p in remote
               if p.get("status") in ("sent","scheduled")}
    rows=[]
    for post_id, entry in state.get("queued",{}).items():
        bufid=entry.get("buffer_id")
        status=by_buffer.get(bufid,"not_visible_in_recent_buffer_list")
        rows.append({"post_id":post_id, "buffer_id":bufid,
                     "buffer_status":status, "format":entry.get("format","unknown")})
    return sorted(rows,key=lambda p:p["post_id"])


def main():
    cfg=json.loads((HERE/"config.json").read_text(encoding="utf-8"))
    state=json.loads((HERE/"state.json").read_text(encoding="utf-8"))
    token=os.environ.get("BUFFER_API_KEY","").strip()
    if not token:
        raise RuntimeError("Missing Buffer secret; cannot verify status.")
    orgid,channelid=choose_x_channel(token)
    if cfg.get("channel_id") != channelid:
        raise RuntimeError("Connected X channel differs from pinned Veripsa; no report.")
    live=list_existing(token,orgid,channelid)
    rows=report(state,live)
    payload={"schema_version":1,
             "checked_at_utc":datetime.now(timezone.utc).isoformat(),
             "source":"buffer_free_posts_api",
             "limitations":"Buffer sent indicates provider state only. X impressions and actual live URL not determined.",
             "items":rows}
    (HERE/"delivery.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    counts={}
    for p in rows:
        counts[p["buffer_status"]]=counts.get(p["buffer_status"],0)+1
    print("Buffer delivery state (NOT X impressions):",counts)
    return 0


if __name__=="__main__":
    try:sys.exit(main())
    except Exception as exc:
        print("Buffer status check failed:",str(exc),file=sys.stderr)
        sys.exit(1)
