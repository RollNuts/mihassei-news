#!/usr/bin/env python3
"""Free read-only summary of Nejimachi's editorial readiness.

No Buffer connection, no post creation, no fabricated analytics.
"""
import json
from pathlib import Path

from review_policy import load_manifest, REVIEW_FILE

HERE=Path(__file__).resolve().parent
manifest=load_manifest()
gate=json.loads((HERE/"ip"/"publication_quality_gate.json").read_text(encoding="utf-8"))
posts=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))["posts"]
state=json.loads((HERE/"state.json").read_text(encoding="utf-8"))["submitted"]
held=json.loads((HERE/"ip"/"quality_held_posts.json").read_text(encoding="utf-8"))
archived={
    result["post_id"] for result in held.get("decisions",[])
    if result.get("result") in ("move_to_draft","not_in_schedule")
}
print("NEJIMACHI EDITORIAL READINESS (read-only local Git data)")
print("New X reservations enabled:",gate.get("new_reservations_enabled") is True)
print("Human-reviewed approvals:",len(manifest["approved"]))
print("Explicitly rejected posts:",len(manifest["rejected"]))
for p in sorted((x for x in posts if x.get("series")=="nejimachi"),
                key=lambda p:p["episode"]):
    path=HERE/"assets"/(p["id"]+".png")
    status=("REJECTED" if p["id"] in manifest["rejected"] else
            "APPROVED" if p["id"] in manifest["approved"] else "UNREVIEWED")
    if p["id"] in archived:
        status+=" / ARCHIVED AS BUFFER DRAFT"
    elif p["id"] in state:
        status+=" / PREVIOUSLY SUBMITTED (verify X separately)"
    print(f"#{p['episode']:02d} {p['title']} :: {status} :: "
          f"image={'PRESENT' if path.is_file() else 'MISSING'}")
print("NOTE: Current Buffer/X live state is not queried and cannot be inferred from Git.")
