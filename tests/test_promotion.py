#!/usr/bin/env python3
"""Promotion tests: registry and GitHub seams are fixtures, never live writes."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.github/scripts/promote-tested-image'


def load():
    loader = importlib.machinery.SourceFileLoader('promotion', str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CaptureTests(unittest.TestCase):
    def test_capture_binds_raw_manifest_and_identity_before_tests(self):
        self.assertTrue(SCRIPT.exists(), 'digest-bound promotion helper is missing')
        m = load()
        raw = b'{"schemaVersion":2}'
        digest = 'sha256:' + hashlib.sha256(raw).hexdigest()

        class Registry:
            def raw(self, ref, missing=False):
                return raw

            def inspect(self, ref):
                self.ref = ref
                return {'Labels': {'org.opencontainers.image.revision': 'a' * 40}}

        registry = Registry()
        record = m.capture(registry, 'ghcr.io/platypuschan/matrix-reworked:latest',
                           'Platypuschan/ns8-matrix-reworked', '123', '1', 'a' * 40, '0.1.12')
        self.assertEqual(record['digest'], digest)
        self.assertEqual(registry.ref, 'ghcr.io/platypuschan/matrix-reworked@' + digest)
        self.assertEqual(record['run_id'], '123')
        self.assertEqual(record['run_attempt'], '1')
        self.assertEqual(record['commit'], 'a' * 40)
        self.assertEqual(record['version'], '0.1.12')


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        self.root = root
        self.git('config', 'user.name', 'Test Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (root / 'CATALOG_VERSION').write_text('0.1.12\n')
        (root / 'imageroot').mkdir()
        (root / 'imageroot/module').write_text('original')
        self.git('add', '.')
        self.git('commit', '-qm', 'release')
        self.sha = self.git('rev-parse', 'HEAD')
        raw = b'{"schemaVersion":2}'
        self.digest = self.m.manifest_digest(raw)
        self.record = dict(schema=1, repository=self.m.REPOSITORY, run_id='123',
                           run_attempt='1', commit=self.sha, version='0.1.12',
                           image=self.m.IMAGE, digest=self.digest)
        outer = self

        class Services:
            def __init__(self):
                self.tags = {}
                self.manifests = {outer.digest: raw}
                self.revisions = {outer.digest: outer.sha}
                self.tag = None
                self.release = None
                self.copies = []
                self.fail_release = False

            def git(self, *args):
                return outer.git(*args)

            def raw(self, ref, missing=False):
                if '@' in ref:
                    return self.manifests[ref.split('@')[1]]
                if ref not in self.tags:
                    if missing:
                        return None
                    raise ValueError('missing image')
                return self.manifests[self.tags[ref]]

            def inspect(self, ref):
                return {'Labels': {'org.opencontainers.image.revision':
                                   self.revisions[ref.split('@')[1]]}}

            def copy(self, source, target):
                self.copies.append((source, target))
                self.tags[target] = source.split('@')[1]

            def tag_commit(self, version):
                return self.tag

            def get_release(self, version):
                return self.release

            def create_release(self, version, revision, record):
                if self.fail_release:
                    raise RuntimeError('fixture GitHub unavailable')
                self.tag = revision
                self.release = dict(tag_name=version, draft=False,
                                    prerelease='-' in version)

            def is_current(self, commit):
                return True

        self.s = Services()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def promote(self, **changes):
        record = dict(self.record, **changes)
        return self.m.promote(self.s, record, self.m.REPOSITORY, '123', '1',
                              self.sha, '0.1.12')

    def test_existing_version_with_changed_release_inputs_is_rejected(self):
        self.s.tags[self.m.IMAGE + ':0.1.12'] = self.digest
        (self.root / 'imageroot/module').write_text('changed')
        self.git('add', '.')
        self.git('commit', '-qm', 'module change without bump')
        self.sha = self.git('rev-parse', 'HEAD')
        raw = b'{"candidate":true}'
        digest = self.m.manifest_digest(raw)
        self.s.manifests[digest] = raw
        self.s.revisions[digest] = self.sha
        self.record.update(commit=self.sha, digest=digest)
        with self.assertRaisesRegex(ValueError, 'changed release inputs'):
            self.promote()
        self.assertEqual(self.s.copies, [])

    def test_every_real_release_path_blocks_reusing_existing_version(self):
        import runpy
        contract = runpy.run_path(str(ROOT / '.github/scripts/check-catalog-version'))
        baseline = self.sha
        for release_path in contract['RELEASE_PATHS']:
            with self.subTest(release_path=release_path):
                # Isolate each path: an earlier imageroot change must not mask
                # an incorrectly accepted runtime/app/build-script change.
                self.git('reset', '--hard', baseline)
                self.s.tags[self.m.IMAGE + ':0.1.12'] = self.digest
                path = self.root / release_path
                if path.is_dir() or '.' not in path.name:
                    path = path / 'promotion-fixture'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('release-effective change')
                self.git('add', '.')
                self.git('commit', '-qm', 'change ' + release_path)
                self.sha = self.git('rev-parse', 'HEAD')
                raw = json.dumps({'candidate': self.sha}).encode()
                digest = self.m.manifest_digest(raw)
                self.s.manifests[digest] = raw
                self.s.revisions[digest] = self.sha
                self.record.update(commit=self.sha, digest=digest)
                with self.assertRaisesRegex(ValueError, 'changed release inputs'):
                    self.promote()
                self.assertEqual(self.s.copies, [])

    def test_source_digest_or_revision_mismatch_blocks_copy(self):
        self.s.revisions[self.digest] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'source revision'):
            self.promote()
        self.s.revisions[self.digest] = self.sha
        self.s.manifests[self.digest] = b'corrupt'
        with self.assertRaisesRegex(ValueError, 'source digest'):
            self.promote()
        self.assertEqual(self.s.copies, [])

    def test_api_404_only_is_missing(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        s = self.m.Services()
        path = 'repos/' + self.m.REPOSITORY + '/releases/tags/0.1.12'
        for code in (401, 403, 429, 500):
            with self.subTest(code=code), patch.dict('os.environ', GH_TOKEN='fixture'), \
                    patch('urllib.request.urlopen', side_effect=HTTPError('https://api.github.com', code, '', {}, None)):
                with self.assertRaises(RuntimeError):
                    s.api(path, missing=True)
        with patch.dict('os.environ', GH_TOKEN='fixture'), \
                patch('urllib.request.urlopen', side_effect=HTTPError('https://api.github.com', 404, '', {}, None)):
            self.assertIsNone(s.api(path, missing=True))

    def test_copy_preserves_all_manifest_digests(self):
        from unittest.mock import patch
        s = self.m.Services()
        with patch.object(s, 'command', return_value=b'') as command:
            s.copy(self.m.IMAGE + '@' + self.digest, self.m.IMAGE + ':0.1.12')
        command.assert_called_once_with('skopeo', 'copy', '--all', '--preserve-digests',
                                        'docker://' + self.m.IMAGE + '@' + self.digest,
                                        'docker://' + self.m.IMAGE + ':0.1.12')

    def test_annotated_git_tags_are_peeled(self):
        from unittest.mock import patch
        s = self.m.Services()
        with patch.object(s, 'api', side_effect=[
                {'object': {'type': 'tag', 'sha': 'b' * 40}},
                {'object': {'type': 'commit', 'sha': self.sha}}]):
            self.assertEqual(s.tag_commit('0.1.12'), self.sha)

    def test_validation_requires_exact_repo_commit_push_and_latest_attempt(self):
        run = dict(path='.github/workflows/validate.yml', event='push', head_sha=self.sha,
                   head_repository={'full_name': self.m.REPOSITORY}, run_number=12,
                   run_attempt=1, status='completed', conclusion='success')
        self.assertTrue(self.m.validated_run([run], self.sha))
        for changes in ({'head_sha': 'b' * 40}, {'event': 'pull_request'},
                        {'head_repository': {'full_name': 'other/repo'}},
                        {'status': 'in_progress'}):
            with self.subTest(changes=changes):
                self.assertFalse(self.m.validated_run([dict(run, **changes)], self.sha))
        with self.assertRaises(ValueError):
            self.m.validated_run([run, dict(run, run_attempt=2, conclusion='failure')], self.sha)

    def test_registry_missing_is_not_auth_or_network_failure(self):
        self.assertTrue(hasattr(self.m, 'Services'), 'strict service adapter missing')
        from unittest.mock import patch
        s = self.m.Services()
        for error in (b'unauthorized', b'connection timeout', b'HTTP 403 forbidden'):
            with self.subTest(error=error), patch.object(self.m.subprocess, 'run', return_value=
                    subprocess.CompletedProcess([], 1, b'', error)):
                with self.assertRaises(RuntimeError):
                    s.raw(self.m.IMAGE + ':0.1.12', missing=True)
        with patch.object(self.m.subprocess, 'run', return_value=
                subprocess.CompletedProcess([], 1, b'', b'reading manifest: manifest unknown')):
            self.assertIsNone(s.raw(self.m.IMAGE + ':0.1.12', missing=True))

    def test_wrong_run_commit_version_image_or_attempt_is_rejected(self):
        for field, value in [('repository', 'other/repo'), ('run_id', '999'),
                             ('run_attempt', '2'), ('commit', 'b' * 40),
                             ('version', '9.9.9'), ('image', 'ghcr.io/other/matrix-reworked'),
                             ('digest', 'not-a-digest')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.promote(**{field: value})
        self.assertEqual(self.s.copies, [])

    def test_previous_correct_publication_is_validated_never_overwritten(self):
        old = self.sha
        self.s.tags[self.m.IMAGE + ':0.1.12'] = self.digest
        self.s.tag = old
        self.s.release = dict(tag_name='0.1.12', draft=False, prerelease=False)
        (self.root / 'workflow').write_text('non-release change')
        self.git('add', '.')
        self.git('commit', '-qm', 'workflow only')
        self.sha = self.git('rev-parse', 'HEAD')
        candidate = b'{"schemaVersion":2,"candidate":true}'
        digest = self.m.manifest_digest(candidate)
        self.s.manifests[digest] = candidate
        self.s.revisions[digest] = self.sha
        self.record.update(commit=self.sha, digest=digest)
        self.promote()
        self.assertEqual(self.s.copies, [])
        self.assertEqual(self.s.tag, old)

    def test_same_revision_different_digest_is_hard_conflict(self):
        raw = b'{"different":true}'
        digest = self.m.manifest_digest(raw)
        self.s.manifests[digest] = raw
        self.s.revisions[digest] = self.sha
        self.s.tags[self.m.IMAGE + ':0.1.12'] = digest
        with self.assertRaisesRegex(ValueError, 'tested digest'):
            self.promote()
        self.assertEqual(self.s.copies, [])

    def test_copy_digest_mismatch_never_creates_release(self):
        self.s.copy = lambda source, target: self.s.tags.update({target: 'bad'})
        self.s.manifests['bad'] = b'changed by registry'
        with self.assertRaisesRegex(ValueError, 'copied manifest'):
            self.promote()
        self.assertIsNone(self.s.release)

    def test_conflicting_git_tag_is_rejected_before_copy(self):
        self.s.tag = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'Git tag conflicts'):
            self.promote()
        self.assertEqual(self.s.copies, [])

    def test_superseded_run_does_not_publish(self):
        self.s.is_current = lambda commit: False
        self.assertIn('superseded', self.promote())
        self.assertEqual(self.s.copies, [])
        self.assertIsNone(self.s.release)

    def test_promotion_copies_only_digest_and_resumes_failed_release(self):
        self.assertTrue(hasattr(self.m, 'promote'), 'promotion state machine is missing')
        self.s.fail_release = True
        with self.assertRaisesRegex(RuntimeError, 'GitHub unavailable'):
            self.promote()
        self.s.fail_release = False
        self.promote()
        self.promote()
        self.assertEqual(self.s.copies, [(self.m.IMAGE + '@' + self.digest,
                                         self.m.IMAGE + ':0.1.12')])
        self.assertEqual(self.s.tag, self.sha)
        self.assertFalse(self.s.release['draft'])


class WorkflowContractTests(unittest.TestCase):
    def test_qemu_and_record_share_resolved_reference(self):
        workflow = (ROOT / '.github/workflows/publish-images.yml').read_text()
        self.assertIn('image_url: ${{ needs.published-digest.outputs.reference }}', workflow)
        self.assertIn('needs: [module, published-digest]', workflow)
        self.assertIn('needs: [module, published-digest, qemu, trivy]', workflow)
        self.assertIn('promote-tested-image checks', workflow)
        self.assertIn('tested-image-${{ github.run_id }}-${{ github.run_attempt }}', workflow)

    def test_update_wrapper_passes_a_repository_for_both_reference_forms(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'lookup.txt'
            shim = root / 'python3'
            shim.write_text('#!/bin/bash\nprintf "%s" "$2" > "$LOOKUP_LOG"\nexit 3\n')
            shim.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], LOOKUP_LOG=str(log))
            for ref in ('ghcr.io/platypuschan/matrix-reworked:latest',
                        'ghcr.io/platypuschan/matrix-reworked@sha256:' + 'a' * 64):
                with self.subTest(ref=ref):
                    result = subprocess.run(['bash', str(ROOT / 'test-module-update.sh'),
                                             '192.0.2.1', ref], env=env,
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(log.read_text(), 'ghcr.io/platypuschan/matrix-reworked')

    def test_update_lookup_strips_digest_not_just_tag(self):
        script = (ROOT / 'test-module-update.sh').read_text()
        self.assertIn('IMAGE_REPOSITORY="${IMAGE_URL%%@*}"', script)
        self.assertIn('previous-release "${IMAGE_REPOSITORY%:*}"', script)


if __name__ == '__main__':
    unittest.main()
