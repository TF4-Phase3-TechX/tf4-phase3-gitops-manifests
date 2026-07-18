# D5-PERF-03 staged resource rollout

These files are reviewed resource profiles, not active Argo CD value files. Do not add all
five profiles to `spec.sources[].helm.valueFiles`: `techx-corp` is one automated Argo CD
Application, so doing that would roll out the whole application at once.

## Controlled rollout

Create a separate reviewed commit/PR for each wave during the approved change window:

```bash
export RUN_ID=D5-$(date -u +%Y%m%dT%H%M%SZ)
export CHANGE_TICKET='<approved-ticket>'
export WINDOW_START_UTC='<ISO-8601 UTC>'
export WINDOW_END_UTC='<ISO-8601 UTC>'

python scripts/resource_rollout.py promote 01
git diff -- environments/production/app-values.yaml
```

Merge and allow Argo CD to sync only that commit. Record rollout status, pod health,
restart/OOM deltas, CPU throttling, Browse/Cart/Checkout SLO queries, and the same-window
dashboard exports under:

```text
docs/evidence/directive-05/official-${RUN_ID}/resource-rollout/wave-01/
```

Write `PASS` to that wave's `RESULT` only when all gates pass. The promoter refuses Wave
02-05 unless the immediately preceding `RESULT` is `PASS`. Repeat in numerical order.

Stop immediately for Pending pods, new CrashLoop/OOM, rollout timeout, material CPU
throttling regression, or Browse/Cart/Checkout SLO regression. Do not promote the next
wave. Generate the reviewed GitOps rollback instruction with:

```bash
python scripts/resource_rollout.py rollback-command 02
```

The rollback is a revert of the affected wave commit followed by Argo CD sync. An emergency
`kubectl rollout undo` is not authoritative because Argo CD will reconcile it.

## Wave ownership

| Wave | Scope | Active values file |
|---|---|---|
| 01 | low-risk stateless | `app-values.yaml` |
| 02 | revenue-critical stateless | `app-values.yaml` |
| 03 | stateful and messaging | `app-values.yaml` |
| 04 | observability | `observability-values.yaml` |
| 05 | remaining exceptions | `app-values.yaml` |
