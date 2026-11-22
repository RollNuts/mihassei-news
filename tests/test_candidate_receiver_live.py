"""Offline release-probe checks; never contact the production MCP in tests."""
import io
import json
import unittest
from scripts.check_candidate_receiver import check, URL


def reply(*, tools=None):
    if tools is None:
        schema = {"type": "object", "properties": {
            "download_url": {"type": "string"}, "file_id": {"type": "string"},
            "mime_type": {"type": "string"}, "file_name": {"type": "string"}},
            "required": ["download_url", "file_id"]}
        tools = [
            {"name": name, "_meta": {"openai/fileParams": ["file"]},
             "inputSchema": {"properties": {"file": schema}},
             "annotations": {"readOnlyHint": name != "stage_candidate_jpeg"}}
            for name in ("probe_candidate_jpeg", "stage_candidate_jpeg", "read_candidate_png")
        ]
        tools[-1]["outputSchema"] = {"properties": {
            "image_url": {"type": "string", "pattern": "^data:image/png;base64,"}},
            "required": ["image_url"]}
        weekly_fields = ["issue", "commit", "path", "expected_sha256"]
        tools.append({
            "name": "read_weekly_candidate_png",
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
            "inputSchema": {"type": "object", "additionalProperties": False,
                            "properties": {field: {"type": "string"} for field in weekly_fields},
                            "required": weekly_fields},
            "outputSchema": tools[-1]["outputSchema"],
        })
    return {"jsonrpc": "2.0", "id": "read-only-file-schema", "result": {"tools": tools}}


class FakeResponse(io.BytesIO):
    status = 200
    headers = {"Content-Type": "application/json; charset=utf-8"}
    def geturl(self):
        return URL


class CandidateReceiverLiveContractTests(unittest.TestCase):
    def test_only_read_only_tools_list_post_is_sent(self):
        calls = []
        def fetch(request, timeout):
            calls.append((request, timeout))
            return FakeResponse(json.dumps(reply()).encode())
        check(fetch=fetch)
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, URL)
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(json.loads(request.data)["method"], "tools/list")
        self.assertEqual(timeout, 12)

    def test_missing_fileparams_is_not_a_success(self):
        bad = reply()
        bad["result"]["tools"][0]["_meta"] = {}
        with self.assertRaisesRegex(RuntimeError, "fileParams"):
            check(fetch=lambda *_args, **_kwargs: FakeResponse(json.dumps(bad).encode()))

    def test_missing_png_byte_output_is_not_a_success(self):
        bad = reply()
        next(tool for tool in bad["result"]["tools"]
             if tool["name"] == "read_candidate_png")["outputSchema"] = {}
        with self.assertRaisesRegex(RuntimeError, "structured byte output"):
            check(fetch=lambda *_args, **_kwargs: FakeResponse(json.dumps(bad).encode()))

    def test_weekly_reader_must_be_read_only_and_require_the_immutable_source(self):
        for change in ("writable", "missing_hash", "file_params", "missing_output"):
            with self.subTest(change=change):
                bad = reply()
                weekly = bad["result"]["tools"][-1]
                if change == "writable":
                    weekly["annotations"]["readOnlyHint"] = False
                elif change == "missing_hash":
                    weekly["inputSchema"]["required"].remove("expected_sha256")
                elif change == "file_params":
                    weekly["_meta"] = {"openai/fileParams": ["file"]}
                else:
                    weekly["outputSchema"] = {}
                with self.assertRaises(RuntimeError):
                    check(fetch=lambda *_args, **_kwargs: FakeResponse(json.dumps(bad).encode()))


if __name__ == "__main__":
    unittest.main()
