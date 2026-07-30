# Mandate 22 GitOps activation and kill switch

Status: bootstrap artifact only. This branch intentionally keeps
`gitops/dry-run` and autonomous remediation disabled.

## CDO bootstrap gate

CDO must record evidence for all items before the activation PR:

1. Reuse the CDO-owned `gitops-promotion-bot-tf4` App, already installed on this
   repository. Do not create a second App.
2. Confirm Metadata read, Contents read/write, Pull requests read/write and
   Checks read. Grant no Administration, Actions or Secrets permission.
3. Store `private-key.pem`, `app-id` and `installation-id` under
   `techx/tf4/aiops-github-app` in AWS Secrets Manager account `511825856493`.
4. Confirm `ExternalSecret/aiops-github-app` is Ready and the key is mounted
   read-only.
5. Apply the required-check ruleset for `validate`,
   `check-pinned-dependencies` and `aiops-remediation-policy` with no bot bypass.
6. Apply the PR/review ruleset with only the GitHub App allowed to bypass the
   human review requirement. Direct push to `main` remains prohibited.
7. Prove AIOps can authenticate and prepare a dry-run plan through
   `aiops-github-proxy`, and cannot reach arbitrary public hosts directly.
8. Complete the three Kind/Argo sandbox rounds and attach JSONL replay output.

## Activation PR

The activation PR changes only the reviewed AIOps image/chart pin and these
values in `components.aiops.envOverrides`:

```yaml
- name: REMEDIATION_MODE
  value: gitops/live
- name: AIOPS_AUTONOMOUS_REMEDIATION_ENABLED
  value: "true"
- name: AIOPS_ALLOWED_DEPLOYMENTS
  value: product-reviews
```

Do not add another service. Do not modify `flagd-values.yaml`, Argo self-heal or
`ignoreDifferences`.

## Kill switch PR

Prepare but do not merge a PR that sets:

```yaml
- name: AIOPS_AUTONOMOUS_REMEDIATION_ENABLED
  value: "false"
```

When a stop condition in ADR-022 occurs, CDO suspends the GitHub App first and
then merges the kill-switch PR. Suspension prevents a transaction during Argo
convergence; the values change makes the stop durable.

## Evidence/claim boundary

Runtime success does not supply an on-call/SRE signature. Until the named CDO
and on-call owners sign ADR-022, report at most evidence level 5 and do not close
TF4AIO-83 as mandate accepted.
