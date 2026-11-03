#!/usr/bin/env python3
"""Read-only Buffer API connectivity test. Never creates or queues a post."""
import os
import sys

from publish import choose_x_channel, list_existing


def main():
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        print("FAIL: BUFFER_API_KEY secret is missing in GitHub Actions.")
        return 1
    try:
        org_id, channel_id = choose_x_channel(token)
        current = list_existing(token, org_id, channel_id)
    except Exception as exc:
        print("FAIL: read-only Buffer check could not complete:", str(exc))
        return 1
    print("SUCCESS: Buffer API key works and an X channel was found.")
    print("SUCCESS: Scheduled/sent post query works; returned", len(current), "posts.")
    print("No posts were created or sent in this test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
