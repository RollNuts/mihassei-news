#!/usr/bin/env python3
"""Read the existing GitHub image workflow's live global stop state."""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPOSITORY = "RollNuts/mihassei-news"
WORKFLOW = "free-cpu-scenes.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW
WORKFLOW_API_PATH = "/actions/workflows/" + WORKFLOW


def check_cloud_images_allowed(read_state=None) -> None:
    """Only a fresh, explicit active response permits a cloud image operation."""
    try:
        if read_state is None:
            if not os.environ.get("GH_TOKEN"):
                raise RuntimeError("GitHub image stop state needs GH_TOKEN")
            result = subprocess.run(
                ["gh", "api", "--method", "GET", "repos/" + REPOSITORY + WORKFLOW_API_PATH],
                check=True, capture_output=True, text=True, timeout=30,
            )
            state = json.loads(result.stdout)
        else:
            state = read_state()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Live GitHub image stop state unavailable; cloud image operation stopped") from exc
    if (not isinstance(state, dict) or state.get("path") != WORKFLOW_PATH
            or state.get("state") != "active"):
        raise RuntimeError("GitHub image workflow is not confirmed active; cloud image operation stopped")


def main() -> int:
    try:
        check_cloud_images_allowed()
        print("GitHub cloud image workflow is active")
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
