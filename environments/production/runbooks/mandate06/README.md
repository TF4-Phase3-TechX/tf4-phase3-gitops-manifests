# Mandate 06 cross-account canary preflight

This directory is not referenced by an Argo CD application and is not automatically applied. CDO08 runs this preflight manually before merging the production values change.

## Verified impact boundary

- Base GitOps revision: `8ad1c02eee654f37a41fd1176ff11c93a087f05b`
- Audited PR head before this runbook was added: `714c87e967a20d77c87d9464244e2c4ee65906db`
- Source chart revision: `0a3e626e083b597d1ceac65e7d3a175607f204f1`
- Helm version: `v3.14.0`
- Base and PR each render 71 resources.
- Canonical render comparison changes only `apps/v1 Deployment/techx-tf4/product-reviews`.
- Shared load-generator and all other workloads remain unchanged.
- The product-reviews values add a native gRPC readiness probe on port `3551`, which the service implements with status `SERVING`.

## Run preflight

Run only inside the approved CDO/AIO window:

```bash
kubectl create -f environments/production/runbooks/mandate06/preflight-job.yaml
kubectl wait --for=condition=complete --timeout=90s \
  job/product-reviews-bedrock-cross-account-preflight -n techx-tf4
kubectl logs job/product-reviews-bedrock-cross-account-preflight -n techx-tf4
```

Expected output contains only metadata:

- `result: PASS`
- account `589077667575`
- role `tf4-product-reviews-bedrock-emergency-target`
- Guardrail version `3`
- latency and token counts

The script intentionally does not print the model response. Abort the rollout if the Job fails, reports another account/role, or emits an IAM, Guardrail, timeout or throttling error.

Cleanup if the TTL controller has not already removed the Job:

```bash
kubectl delete job product-reviews-bedrock-cross-account-preflight -n techx-tf4
```

## Rollout rule

Merge is the deployment gate because the Argo application has automated sync and self-heal. Do not merge before platform-owner approval, successful preflight and recorded final GO. The values change itself creates a new ReplicaSet; do not issue a second manual rollout restart unless Argo fails to roll the changed pod template.

Rollback must restore both sides coherently:

1. Restore Guardrail values to `wckqh9dms6qa:1` through GitOps.
2. CDO08 removes `targetRoleArn` from the Pod Identity association.
3. Verify the canonical safe-unavailable behavior while account `511825856493` quotas remain zero.
