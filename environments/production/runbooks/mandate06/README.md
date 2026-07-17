# Mandate 06 remediation canary preflight

This directory is not referenced by an Argo CD application and is not
automatically applied. A CDO owner runs the Job manually inside a named
CDO/AIO window before the remediation canary promotion is merged.

## Release under test

- Source commit: `d1c463241d36743fbcdb9ee57028aec96f0b7914`
- Product-reviews image tag: `d1c4632-product-reviews`
- Immutable image digest:
  `sha256:f8a938d6822a1e689dde1f8df01123635dcbd68bea32fa681ff8e439061aaa92`
- Model: `us.amazon.nova-2-lite-v1:0`
- Temporary canary account: `589077667575`
- Guardrail: `e2svpiawj1v5:3`

The previous canary was rolled back first. The source-only Pod Identity
association and Guardrail `wckqh9dms6qa:1` were read back on the healthy
revision-21 workload before this fresh canary was prepared.

## Impact boundary

The Argo-managed change is limited to `Deployment/product-reviews`:

- image `c16ecbe-product-reviews` to `d1c4632-product-reviews`; and
- Guardrail `wckqh9dms6qa:1` to `e2svpiawj1v5:3`.

The Service, load generator, other workloads, Secrets and RBAC are unchanged.
The preflight manifest itself is a manual runbook artifact and is not applied
by Argo CD.

## CDO identity gate

Before running the Job, CDO must create the approved cross-account Pod
Identity association for namespace `techx-tf4`, ServiceAccount
`product-reviews-bedrock`, source role
`arn:aws:iam::511825856493:role/tf4-product-reviews-bedrock`, and target role
`arn:aws:iam::589077667575:role/tf4-product-reviews-bedrock-emergency-target`.

Record the new association ID, source/target role ARNs, `modifiedAt`, named CDO
owner and UTC start/expiry. Abort if read-back differs from the approved
identity pair.

## Production-shaped non-routing preflight

The Job imports the shipped `BedrockAdapter` and `validate_grounded_output`
directly from the exact remediation image, then sends one synthetic request
with:

- application system prompt plus the existing Secret-backed system canary;
- guarded grounding source and guarded query;
- deterministic synthetic product/review context;
- forced non-action `emit_grounded_answer` tool;
- Nova-compatible top-level tool schema;
- `temperature=0`, `maxTokens=512`, zero SDK retries and 4.5-second deadline;
- pinned Guardrail version 3; and
- the production PII, canary-leak, schema and exact evidence-quote validator.

It prints only account/role, image/model/Guardrail, response-contract, latency,
token and citation-count metadata. It never prints the prompt, context, answer,
evidence quotes, model response, credentials or Secret values.

Run only inside the approved window:

```bash
kubectl create -f environments/production/runbooks/mandate06/preflight-job.yaml
kubectl wait --for=condition=complete --timeout=90s \
  job/product-reviews-bedrock-cross-account-preflight -n techx-tf4
kubectl logs job/product-reviews-bedrock-cross-account-preflight -n techx-tf4
```

Expected metadata includes:

- `result: PASS`
- account `589077667575`
- role `tf4-product-reviews-bedrock-emergency-target`
- the immutable image digest above
- Guardrail version `3`
- `stop_reason: tool_use`
- `contract_stage: tool_input_dict`
- `decision: answered` with exact evidence for both durability and comfort
- non-zero token counts and latency no greater than 4500 ms
- `content_retained: false`

Abort if the Job fails, reports another account/role/image/Guardrail, returns a
different response contract, exceeds the deadline or fails exact-citation
validation.

Cleanup if the TTL controller has not already removed the Job:

```bash
kubectl delete job product-reviews-bedrock-cross-account-preflight -n techx-tf4
```

## Promotion and rollback gates

After preflight PASS, the platform/CDO reviewer records final GO, marks the
canary PR Ready and merges through the protected path. Do not issue a manual
rollout restart; the pod-template change creates the ReplicaSet.

AIO1 then runs the supported, unsupported, stored-injection, PII/system-canary,
action-request and provider-failure probes. Prometheus/OpenSearch/Jaeger must
show sanitized metadata, non-zero token/cost counters, application p95 below
the five-second budget and no Storefront SLO regression.

Any hard-gate failure triggers coordinated rollback:

1. CDO restores a source-only Pod Identity association and verifies
   `targetRoleArn=null`.
2. GitOps restores Guardrail `wckqh9dms6qa:1` and the previous approved image.
3. Argo/workload readiness and canonical safe-unavailable behavior are read
   back and the recovery duration is recorded.

There is no real-to-mock fallback.
