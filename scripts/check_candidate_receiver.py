#!/usr/bin/env python3
"""Read-only production proof of the separate ChatGPT image-file MCP schema.

POST is a JSON-RPC tools/list read, never a file transfer or a write call.
It does not prove an actual ChatGPT signed file is deliverable.
"""
from __future__ import annotations

import json
import urllib.request

URL = "https://veripsa.com/api/image-candidate/mcp"
MAX_BODY = 24 * 1024


def check(*, fetch=urllib.request.urlopen):
    payload = json.dumps({"jsonrpc": "2.0", "id": "read-only-file-schema",
                          "method": "tools/list", "params": {}},
                         separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(URL, data=payload, method="POST", headers={
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
        "Cache-Control": "no-cache",
        "User-Agent": "Veripsa-ReadOnly-Candidate-Schema/1.0",
    })
    with fetch(request, timeout=12) as response:
        if response.status != 200 or response.geturl() != URL:
            raise RuntimeError("Candidate MCP schema endpoint is unavailable/redirected")
        if "application/json" not in response.headers.get("Content-Type", "").lower():
            raise RuntimeError("Candidate MCP schema has wrong Content-Type")
        wire = response.read(MAX_BODY + 1)
        if not wire or len(wire) > MAX_BODY:
            raise RuntimeError("Candidate MCP schema response is empty/too large")
    document = json.loads(wire)
    if document.get("jsonrpc") != "2.0" or document.get("id") != "read-only-file-schema":
        raise RuntimeError("Candidate MCP JSON-RPC identity mismatch")
    tools = document.get("result", {}).get("tools")
    file_tools = ("probe_candidate_jpeg", "stage_candidate_jpeg", "read_candidate_png")
    weekly_reader = "read_weekly_candidate_png"
    if not isinstance(tools, list) or len(tools) != len(file_tools) + 1:
        raise RuntimeError("Candidate MCP must expose three file tools and the weekly PNG reader")
    by_name = {tool.get("name"): tool for tool in tools}
    if set(by_name) != {*file_tools, weekly_reader}:
        raise RuntimeError("Candidate MCP tool names differ")
    for name in file_tools:
        tool = by_name[name]
        if tool.get("_meta", {}).get("openai/fileParams") != ["file"]:
            raise RuntimeError(name + ": fileParams missing")
        schema = tool.get("inputSchema", {}).get("properties", {}).get("file", {})
        if schema.get("type") != "object" or schema.get("required") != ["download_url", "file_id"]:
            raise RuntimeError(name + ": required file object fields differ")
        if set(schema.get("properties", {})) != {"download_url", "file_id", "mime_type", "file_name"}:
            raise RuntimeError(name + ": allowed file object fields differ")
    if by_name["probe_candidate_jpeg"].get("annotations", {}).get("readOnlyHint") is not True:
        raise RuntimeError("Candidate probe is not marked read-only")
    if by_name["stage_candidate_jpeg"].get("annotations", {}).get("readOnlyHint") is not False:
        raise RuntimeError("Candidate stage tool falsely claims to be read-only")
    weekly = by_name[weekly_reader]
    schema = weekly.get("inputSchema", {})
    fields = {"issue", "commit", "path", "expected_sha256"}
    props = schema.get("properties", {})
    if (schema.get("type") != "object" or schema.get("additionalProperties") is not False
            or set(schema.get("required", [])) != fields or set(props) != fields
            or any(prop.get("type") != "string" for prop in props.values())
            or "openai/fileParams" in weekly.get("_meta", {})
            or weekly.get("annotations", {}).get("destructiveHint") is not False):
        raise RuntimeError("Weekly PNG reader input or annotations differ")
    for name in ("read_candidate_png", weekly_reader):
        png = by_name[name]
        if png.get("annotations", {}).get("readOnlyHint") is not True:
            raise RuntimeError(name + ": PNG reader is not marked read-only")
        output = png.get("outputSchema", {})
        image = output.get("properties", {}).get("image_url", {})
        if "image_url" not in output.get("required", []) or image.get("pattern") != "^data:image/png;base64,":
            raise RuntimeError(name + ": PNG reader lacks its structured byte output")
    print("Public candidate MCP file and weekly PNG schemas verified (tools/list only).")


if __name__ == "__main__":
    check()
