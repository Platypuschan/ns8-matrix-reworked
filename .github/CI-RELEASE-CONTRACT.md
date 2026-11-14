# Shared CI / release contract

These nine Platypuschan repositories share the same release algorithm:
`ns8-{ntfy-reworked,maubot,adguard-home,matrix-reworked,matrix-discord-bridge,gitea-reworked,obsidian-livesync,organizr-reworked,proxmox-datacenter-manager}`.
Fail2ban, Traefik, Vaultwarden, IdP and Samba have different contracts and are
not templates for this group.

## Canonical code and bounded module configuration

- `.github/scripts/check-catalog-version`: one SemVer / real-Git algorithm.
  `RELEASE_PATHS` is the module configuration near the top; promotion imports
  this exact list. A base genuinely predating CATALOG_VERSION is a first
  release, not a reason to swallow arbitrary Git failures.
- `.github/scripts/previous-release`: one baseline-selection algorithm with
  stable-only defaults and optional `--strict-older`. `NO_RELEASE` retains the
  existing update wrapper's exit-status contract; only PDM enables prerelease
  fallback when no eligible stable baseline exists. Registry failures never
  mean "no previous release".
- `.github/scripts/promote-tested-image`: one digest-bound state machine.
  Image, repository and `REQUIRED_WORKFLOWS` are the configuration at the top.
  Organizr additionally requires same-commit APK runtime and Renovate policy
  workflows. No same-commit gate is removed by this consolidation.
- `.github/workflows/publish-images.yml`: the module prerequisite jobs are in
  the bounded `MODULE PREREQUISITES` section at the top of `jobs`. Their
  original names, steps and guards remain. The small `MODULE CONFIG` block
  wires `publish-images.needs` and retains Gitea/Organizr first-run guards.
  GitHub requires literal job IDs here; this is deliberately not hidden in
  string evaluation, a dynamic workflow or a repository variable.
- `clean-registry.yml`, `test-module.yml`, and `publish-catalog-version.yml`
  share code after replacing the module's identity. Cleanup still keeps
  stable/prerelease version tags and active branches; PR cleanup is dry-run.

| Module | Extra release paths | Publisher prerequisite | No-release status | Baseline exception | Attestation exception |
| --- | --- | --- | --- | --- | --- |
| ntfy-reworked | none | none | 1 | none | none |
| maubot | runtime | runtime job | 3 | none | none |
| adguard-home | none | none | 3 | none | none |
| matrix-reworked | none | none | 3 | none | none |
| matrix-discord-bridge | none | none | 3 | commit-tag runtime remains in build-images.sh | none |
| gitea-reworked | none | none; retain first-run guard | 1 | none | apidoc validation stays in Validate |
| obsidian-livesync | none | none | 3 | none | none |
| organizr-reworked | runtime | runtime job; retain first-run guards | 1 | wrapper requires --strict-older | Validate + APK runtime + Renovate policy |
| proxmox-datacenter-manager | app | app image, native packaged UI and both module UI browsers | 1 | prerelease fallback | browser gates remain in app prerequisite |

Every module also includes `imageroot`, `ui`, and `build-images.sh` in
RELEASE_PATHS. Neither these paths nor CATALOG_VERSION are changed here.
Update wrappers and application build/runtime recipes are unchanged.

## Common safety properties

The publisher resolves the raw manifest digest and verifies its revision
before QEMU. All four existing QEMU status contexts receive that same
`image@sha256:...`. Scanning and same-commit validation precede attestation;
the metadata identity includes repository, run, attempt, SHA, version and
digest. Promotion consumes only its triggering run's attestation, serializes
writers, preserves digests, validates existing tags without overwriting, and
reads back registry/tag/release state. A PR does not activate the default-
branch `workflow_run` promoter. GHCR offers no atomic tag create-if-absent
against unrelated external writers.

Hosted jobs in these shared workflows use `ubuntu-24.04`, the existing common
promotion baseline. NethServer caller workflows use the shared immutable
`e35baed847065163e5fcb2fe396c968aa413827b` revision (the existing publisher pin,
including its later Python requirements diagnostic). Gitea's older caller
pins and floating test-module v1 pins are aligned to that revision. Direct
checkout/setup-node references in module prerequisites are also immutable.
This is caller pinning, not a claim that every nested upstream action is pinned.
Required check names, rulesets, secrets and repository settings are unchanged.

## Verification and future changes

Run `python3 -B -m unittest discover -s tests`, actionlint, yamllint, shell
syntax checks and the real version checker both with `--base origin/main`
and without arguments **after committing**. Promotion tests use real Git
repositories and offline registry/GitHub seams, never live release writes.
`test_shared_release_contract.py` checks baseline modes, every configured
same-commit workflow gate and registry-package identity.

The shared project tool `ns8_chain_conformance.py` reports seven files equal
after replacing exact identity tokens and excluding only balanced, explicitly
marked module sections. Its report is read-only, includes raw source hashes
and missing repositories, and is a drift detector, not approval of module
configuration. Always review excluded sections separately. Do not "fix"
drift by wrapping shared security logic in a new module section.

No merge, release, workflow dispatch or settings change is part of this PR.
Existing feature-branch push CI may publish its normal candidate images;
that is distinct from catalog release promotion.
