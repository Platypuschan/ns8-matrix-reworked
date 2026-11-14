"""Shared release-contract regressions; service boundaries stay offline."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'), str(ROOT / '.github/scripts' / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class SharedReleaseContractTests(unittest.TestCase):
    def test_release_selection_supports_strict_and_prerelease_modes(self):
        m = load('previous-release')
        tags = ['0.1.0-rc.1', '0.1.0-rc.2', '0.1.0', '0.2.0', 'latest']
        self.assertEqual(m.newest_release(tags, '0.1.0', strict_older=True), None)
        self.assertEqual(m.newest_release(tags, '0.1.0-rc.2', stable_only=False), '0.1.0-rc.2')
        self.assertEqual(m.newest_release(tags, '0.1.0-rc.2', stable_only=False, strict_older=True), '0.1.0-rc.1')
        self.assertEqual(m.newest_release(tags, '0.1.0'), '0.1.0')

    def test_each_configured_workflow_is_a_mandatory_same_commit_gate(self):
        m = load('promote-tested-image')
        sha = 'a' * 40
        runs = [dict(path=path, event='push', head_sha=sha,
                     head_repository={'full_name': m.REPOSITORY}, run_number=10,
                     run_attempt=1, status='completed', conclusion='success')
                for path in m.REQUIRED_WORKFLOWS]
        self.assertIn('.github/workflows/validate.yml', m.REQUIRED_WORKFLOWS)
        self.assertTrue(m.validated_workflows(runs, sha))
        for run in runs:
            with self.subTest(workflow=run['path']):
                self.assertFalse(m.validated_workflows([r for r in runs if r != run], sha))
                with self.assertRaises(ValueError):
                    m.validated_workflows([dict(r, conclusion='failure') if r == run else r for r in runs], sha)
                self.assertFalse(m.validated_workflows([dict(r, status='in_progress') if r == run else r for r in runs], sha))
                self.assertFalse(m.validated_workflows([dict(r, head_sha='b' * 40) if r == run else r for r in runs], sha))

    def test_registry_operations_target_this_module_package(self):
        m = load('promote-tested-image')
        package = m.IMAGE.rsplit('/', 1)[1]
        cleanup = (ROOT / '.github/workflows/clean-registry.yml').read_text()
        publisher = (ROOT / '.github/workflows/publish-images.yml').read_text()
        self.assertIn('PACKAGE: ' + package + '\n', cleanup)
        self.assertIn('/' + package + ':${REF_NAME', publisher)

    def test_first_release_base_really_has_no_catalog_version(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            git('init', '-q')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Release fixture')
            (root / 'README.md').write_text('No module yet\n')
            git('add', '.')
            git('commit', '-qm', 'empty base')
            base = git('rev-parse', 'HEAD')
            self.assertEqual(git('ls-tree', '--name-only', base, '--', 'CATALOG_VERSION'), '')
            (root / 'CATALOG_VERSION').write_text('0.1.0\n')
            (root / 'imageroot').mkdir()
            (root / 'imageroot/module').write_text('first release\n')
            git('add', '.')
            git('commit', '-qm', 'introduce module')
            self.assertEqual(load('check-catalog-version').check(base, cwd=root), [])

    def test_git_failure_is_not_mistaken_for_first_release(self):
        import subprocess
        from unittest.mock import patch
        m = load('check-catalog-version')
        def git(*args, **kwargs):
            if args[0] == 'show' and args[1].startswith('HEAD:'):
                return '0.1.0'
            if args[0] == 'merge-base':
                return 'base'
            if args[0] == 'diff':
                return 'imageroot/module'
            if args[0] == 'ls-tree':
                return 'CATALOG_VERSION'
            raise subprocess.CalledProcessError(128, ['git', *args])
        with patch.object(m, 'git', side_effect=git):
            with self.assertRaises(subprocess.CalledProcessError):
                m.check('base')
