"""Keep real delivery checks token-free; isolate check-recording permissions."""
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PhotoCompletionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.markup = (ROOT / '.github/workflows/verify-public-photos.yml').read_text()
        self.delivery, remaining = self.markup.split('\n  photo_completion:\n', 1)
        self.recorder, self.finalizer = remaining.split('\n  finalize:\n', 1)

    def test_existing_delivery_job_keeps_read_only_permissions_and_no_api_write(self):
        self.assertIn('permissions:\n  contents: read\njobs:', self.delivery)
        for forbidden in ('GH_TOKEN:', 'checks: write', 'contents: write',
                          'github-script', 'gh api', '--method POST'):
            self.assertNotIn(forbidden, self.delivery)
        for command in ('wait_for_live_revision.py --allow-docs-only', 'check_live.py',
                        'check_share_cards.py', 'report_photo_health.py'):
            self.assertIn(command, self.delivery)
        self.assertNotIn('GH_TOKEN:', self.recorder)

    def allowed(self, result='success', ref='refs/heads/main', repo='RollNuts/mihassei-news', private=False, cancelled=False):
        expression = re.search(r'(?m)^    if: \$\{\{ (.+) \}\}$', self.recorder).group(1)
        expression = expression.replace('!cancelled()', 'not was_cancelled').replace('&&', 'and').replace('false', 'False')
        return eval(expression, {'__builtins__': {}}, {
            'was_cancelled': cancelled, 'needs': SimpleNamespace(check=SimpleNamespace(result=result)),
            'github': SimpleNamespace(ref=ref, repository=repo,
                event=SimpleNamespace(repository=SimpleNamespace(private=private)))})

    def test_recording_waits_for_successful_live_verification_of_public_main(self):
        self.assertIn('    needs: check\n', self.recorder)
        self.assertTrue(self.allowed())
        for result in ('failure', 'skipped', 'cancelled', None):
            self.assertFalse(self.allowed(result=result))
        self.assertFalse(self.allowed(ref='refs/heads/other'))
        self.assertFalse(self.allowed(repo='someone/fork'))
        self.assertFalse(self.allowed(private=True))
        self.assertFalse(self.allowed(cancelled=True))

    def test_only_isolated_recorder_has_check_permission(self):
        permissions = self.recorder.split('    permissions:\n', 1)[1].split('    steps:', 1)[0]
        self.assertEqual(permissions, '      contents: read\n      checks: write\n')
        self.assertEqual(self.markup.count('checks: write'), 1)
        self.assertNotIn('contents: write', self.delivery + self.recorder)
        self.assertNotIn('pull-requests: write', self.delivery + self.recorder)
        self.assertIn('timeout-minutes: 3', self.recorder)
        self.assertIn('persist-credentials: false', self.recorder)

    def test_exact_current_run_artifact_cannot_overwrite_checkout(self):
        self.assertIn('name: live-photo-health-${{ github.run_id }}', self.recorder)
        self.assertIn('path: ${{ runner.temp }}/photo-health', self.recorder)
        self.assertIn('ref: ${{ needs.check.outputs.sha }}', self.recorder)
        self.assertIn('EXPECTED_SHA: ${{ needs.check.outputs.sha }}', self.recorder)
        self.assertIn('--expected-sha "$EXPECTED_SHA"', self.recorder)
        self.assertIn('--report "$RUNNER_TEMP/photo-health/live-photo-health.json"', self.recorder)
        self.assertNotIn('continue-on-error', self.recorder)
        self.assertLess(self.recorder.index('photo-check-payload'), self.recorder.index('github.rest.checks.create'))

    def test_api_mutation_only_records_one_revision_bound_check(self):
        self.assertEqual(self.recorder.count('github.rest.'), 1)
        self.assertIn('github.rest.checks.create({', self.recorder)
        self.assertIn('payload.head_sha !== process.env.VERIFIED_SHA', self.recorder)
        self.assertIn("payload.name !== 'Photograph completion'", self.recorder)
        self.assertIn('payload.details_url !== expectedUrl', self.recorder)
        self.assertIn('head_sha: process.env.VERIFIED_SHA', self.recorder)
        self.assertIn("!['success', 'failure'].includes(payload.conclusion)", self.recorder)
        self.assertIn('retries: 0', self.recorder)
        self.assertNotIn('core.setFailed', self.recorder)  # A legitimate backlog is a separate red check, not a delivery failure.

    def test_publication_handoff_cannot_cancel_or_run_as_a_delivery_check(self):
        self.assertIn("github.event_name == 'push' || github.event_name == 'workflow_dispatch'", self.delivery)
        self.assertIn("inputs.finalize_pr == ''", self.delivery)
        self.assertIn("format('publication-finalize-{0}', github.run_id)", self.delivery)
        self.assertIn('ref: refs/heads/main', self.finalizer)
        self.assertIn('persist-credentials: false', self.finalizer)
        self.assertNotIn('actions/download-artifact', self.finalizer)
        self.assertNotIn('github.event.pull_request.head.sha', self.finalizer)
        self.assertEqual(self.markup.count('contents: write'), 1)
        self.assertIn('actions: write', self.finalizer)


if __name__ == '__main__':
    unittest.main()
