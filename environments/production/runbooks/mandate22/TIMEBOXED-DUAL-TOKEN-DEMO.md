# Mandate 22 time-boxed dual-token demo

Status: approved demo workaround for 2026-07-31 only.

This profile exists because Nam does not have GitHub App Manager permission and
the production App cannot receive `Checks: read` before the demo window.

## Identity and boundary

- Creator: `c0mmie-b0msh3ll`
- Reviewer: `tdinhthong127`
- Repository: `TF4-Phase3-TechX/tf4-phase3-gitops-manifests` only
- Policy expiry: `2026-08-01T16:59:59Z`
- Claim: assisted two-account scripted GitOps demo, not CDO-owned autonomous
  remediation

The creator opens only `aiops/remediation/<incident-id>` and
`aiops/compensation/<incident-id>` PRs. The reviewer reads the three required
checks, submits an approval tied to the current head SHA, then squash-merges.
The controller fails closed if the identities match, a check fails, the PR
closes unmerged, the base managed fields move, or the target Lease is lost.

## Runtime configuration

The activation override must set:

```yaml
- name: AIOPS_GITHUB_AUTH_MODE
  value: token-files
- name: AIOPS_GITOPS_MERGE_STRATEGY
  value: dual-token
- name: AIOPS_GITHUB_CREATOR_LOGIN
  value: c0mmie-b0msh3ll
- name: AIOPS_GITHUB_REVIEWER_LOGIN
  value: tdinhthong127
- name: AIOPS_TIMEBOXED_DEMO_ACKNOWLEDGED
  value: "true"
```

Mount `creator-token` and `reviewer-token` read-only from the demo credential
Secret. Never commit token values. The production defaults remain
`AIOPS_GITHUB_AUTH_MODE=app` and `AIOPS_GITOPS_MERGE_STRATEGY=auto`.

## After the demo

Keep the tokens only through the agreed next-day demonstration window. Do not
claim that their existence replaces the CDO App/ruleset/secret bootstrap.
Return the deployment to dry-run or the separately approved production App
profile after evidence capture.
