// Exercise native Renovate RE2, package rules and release-version updates.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const base = '/usr/local/renovate/dist/';
const { resolveConfigPresets } = await import(base + 'config/presets/index.js');
const { applyPackageRules } = await import(base + 'util/package-rules/index.js');
const { extractPackageFile } = await import(base + 'modules/manager/custom/regex/index.js');
const { bumpVersions } = await import(base + 'workers/repository/update/branch/bump-versions.js');
const { GlobalConfig } = await import(base + 'config/global.js');
const { setPlatformScmApi } = await import(base + 'modules/platform/scm.js');
const { generateBranchName } = await import(base + 'workers/repository/updates/branch-name.js');
const { getConfig } = await import(base + 'config/defaults.js');
const raw = JSON.parse(await fs.readFile('renovate.json', 'utf8'));
const { config } = await resolveConfigPresets(raw);
assert.equal(config.automerge, false);
const fixtures = [["build-images.sh", 9], ["imageroot/pypkg/matrix_setup.py", 10]];
let extracted = 0;
for (const [file, expectedCount] of fixtures) {
  const source = await fs.readFile(file, 'utf8');
  const managers = raw.customManagers.filter(m => m.managerFilePatterns.some(p => new RegExp(p.slice(1, -1)).test(file)));
  const deps = [];
  for (const manager of managers) {
    const result = await extractPackageFile(source, file, manager);
    assert.ok(result?.deps.length, JSON.stringify(manager));
    deps.push(...result.deps);
    // Every real pin is replayed independently with and without a digest.
    for (const dep of result.deps) {
      assert.ok(source.includes(dep.currentValue));
      assert.ok(!dep.currentValue.includes('@'));
      const digest = 'sha256:' + 'a'.repeat(64);
      const fixture = source.replace(`${dep.depName}:${dep.currentValue}`, `${dep.depName}:${dep.currentValue}@${digest}`);
      const replay = await extractPackageFile(fixture, file, manager);
      const updated = replay.deps.find(d => d.depName === dep.depName);
      assert.equal(updated.currentValue, dep.currentValue);
      assert.equal(updated.currentDigest, digest);
    }
  }
  assert.equal(deps.length, expectedCount, file);
  assert.equal(new Set(deps.map(d => d.depName)).size, expectedCount, file);
  extracted += deps.length;
  console.log(`PASS native extraction: ${file}: ${deps.length} pins, tag-only + digest`);
}

async function rule(input) {
  return applyPackageRules({ ...config, packageName: input.depName, ...input });
}
const nodes = [
  { manager: 'custom.regex', packageFile: 'build-images.sh', depName: 'docker.io/library/node', datasource: 'docker' },
  { manager: 'dockerfile', packageFile: 'ui/Containerfile', depName: 'node', datasource: 'docker' },
  { manager: 'dockerfile', packageFile: 'ui/Containerfile', depName: 'docker.io/library/node', datasource: 'docker' },
  { manager: 'github-actions', packageFile: '.github/workflows/validate.yml', depName: 'node', packageName: 'actions/node-versions', depType: 'uses-with', datasource: 'github-tags' },
];
for (const input of nodes) {
  for (const updateType of ['patch', 'minor', 'major']) {
    const r = await rule({ ...input, updateType });
    assert.equal(r.groupName, 'Node.js toolchain');
    assert.equal(r.allowedVersions, '<25', 'Node 25+ requires a separate Corepack migration');
    assert.equal(r.automerge, false);
    if (updateType === 'major') assert.equal(r.dependencyDashboardApproval, true);
  }
}
console.log('PASS Node grouping: short/full Docker names and setup-node; Corepack ceiling');
const ui = await rule({ manager: 'npm', packageFile: 'ui/package.json', depName: 'vue', updateType: 'patch' });
const runtime = await rule({ manager: 'custom.regex', packageFile: 'build-images.sh', depName: "ghcr.io/element-hq/synapse", datasource: 'docker', updateType: 'patch' });
const bridge = await rule({ manager: 'custom.regex', packageFile: 'imageroot/pypkg/matrix_setup.py', depName: 'dock.mau.dev/mautrix/whatsapp', datasource: 'docker', updateType: 'patch' });
const action = await rule({ manager: 'github-actions', packageFile: '.github/workflows/validate.yml', depName: 'actions/checkout', depType: 'action', updateType: 'minor' });
assert.equal(ui.groupName, 'UI dependencies (non-major)');
assert.equal(action.groupName, 'GitHub Actions');
assert.equal(action.bumpVersions?.length ?? 0, 0);
for (const updateType of ['patch', 'minor', 'major', 'digest', 'pinDigest']) {
  for (const input of [ui, runtime, bridge, action, ...nodes]) {
    const r = await rule({ ...input, updateType });
    assert.equal(r.automerge, false);
    if (updateType === 'major') assert.equal(r.dependencyDashboardApproval, true);
  }
}
for (const input of [runtime, bridge]) for (const updateType of ['patch', 'minor', 'major', 'digest', 'pinDigest']) assert.equal((await rule({ ...input, updateType })).dependencyDashboardApproval, true);
console.log('PASS runtime and bridge updates require dashboard approval for every update type');
const branchUpdate = { ...getConfig(), ...ui };
generateBranchName(branchUpdate);
assert.match(branchUpdate.branchName.replaceAll('/', '-'), /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/);
console.log('PASS manual merges, major approvals and OCI-safe UI branch');
// Keep the release-path rule aligned with the actual gate, not a second hardcoded list.
const gate = await fs.readFile('.github/scripts/check-catalog-version', 'utf8');
const paths = [...gate.match(/RELEASE_PATHS = \(([^)]+)\)/)[1].matchAll(/"([^"]+)"/g)].map(m => m[1]);
const bumpRule = raw.packageRules.find(r => r.bumpVersions);
for (const path of paths) assert.ok(bumpRule.matchFileNames.includes(path) || bumpRule.matchFileNames.includes(path + '/**'), path);
GlobalConfig.set({ localDir: process.cwd(), platform: 'local' });
setPlatformScmApi('local');
const oldVersion = (await fs.readFile('CATALOG_VERSION', 'utf8')).trim();
const expected = oldVersion.replace(/(\d+)$/, n => String(Number(n) + 1));
const lock = await rule({ manager: 'npm', packageFile: 'ui/package.json', depType: 'lockFileMaintenance', updateType: 'lockFileMaintenance' });
for (const [name, upgrades, packages, artifacts, shouldBump] of [
  ['manifest', [ui], ['ui/package.json'], [], true],
  ['runtime', [runtime], ['build-images.sh'], [], true],
  ['lock-only', [lock], [], ['ui/yarn.lock'], true],
  ['imageroot', [bridge], ['imageroot/pypkg/matrix_setup.py'], [], true],
  ['group-action-first', [action, runtime, ui], ['.github/workflows/validate.yml', 'build-images.sh', 'ui/package.json'], [], true],
  ['workflow-only', [action], ['.github/workflows/validate.yml'], [], false],
  ['no-op', [ui], [], [], false],
]) {
  const change = path => ({ type: 'addition', path, contents: 'test update\n' });
  const branch = { ...config, bumpVersions: upgrades[0].bumpVersions, upgrades, updatedPackageFiles: packages.map(change), updatedArtifacts: artifacts.map(change) };
  await bumpVersions(branch);
  assert.ok(!branch.artifactErrors?.length, JSON.stringify(branch.artifactErrors));
  const bumped = [...branch.updatedPackageFiles, ...branch.updatedArtifacts].filter(f => f.path === 'CATALOG_VERSION');
  assert.equal(bumped.length, shouldBump ? 1 : 0, name);
  if (shouldBump) assert.equal(bumped[0].contents.trim(), expected, name);
  console.log(`PASS native bumpVersions: ${name}`);
}
console.log(`Renovate policy passed: ${extracted} extracted pins; 7 release scenarios.`);
