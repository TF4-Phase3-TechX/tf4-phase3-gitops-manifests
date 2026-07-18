# D5-PERF-05 post-enforcement performance regression

This is a fail-closed evidence runner. It captures Kubernetes state, HPA status, raw
load-generator logs, point-in-time Prometheus responses, and range responses covering the
exact same test window. It compares the result with a pre-remediation `summary.json` and
writes only `PASS` or `FAIL`; unavailable metrics never pass.

## Preconditions

- Run only in the approved production change window after remediation and admission
  enforcement are complete and Argo CD is Healthy/Synced.
- The reviewed load-generator profile must be running unchanged from the baseline run.
- Confirm the Prometheus metric and label names in `config.json` against the deployed chart.
  Do not weaken a threshold to make a run pass.
- Keep an immutable pre-remediation run made with the same config, duration, traffic profile,
  application revision, and comparable time-of-day/load conditions.
- Port-forward Prometheus locally. For the chart's default service this is typically:

  ```bash
  kubectl -n techx-observability port-forward svc/prometheus 9090:9090
  ```

## Official run

Use a unique UTC run ID. The rolling restart is intentionally opt-in because it mutates the
Deployment pod template. Argo CD remains authoritative; the restart tests that the configured
`maxSurge` pod can be scheduled and the rollout becomes ready without a Pending pod.

```bash
RUN_ID=D5-$(date -u +%Y%m%dT%H%M%SZ)
python scripts/performance_regression.py \
  --run-id "$RUN_ID" \
  --baseline docs/evidence/directive-05/official-<BASELINE_RUN_ID>/performance-regression/summary.json \
  --duration-seconds 600 \
  --execute-surge-restart
```

The command exits `0` only when every gate passes and writes evidence to:

```text
docs/evidence/directive-05/official-<RUN_ID>/performance-regression/
├── RESULT
├── summary.json
└── raw/
    ├── load-generator.log
    ├── start/
    ├── end/
    ├── same-window/
    └── surge/
```

`same-window/` is the dashboard-source evidence: raw Prometheus range data for the exact
start/end timestamps recorded in `summary.json`. Export the human-facing Grafana panels for
that same UTC interval alongside these files if the change record requires screenshots.

## Gates

- Browse, Cart, and Checkout error ratios do not exceed 1.10x baseline.
- Storefront p95 is below one second and does not exceed 1.10x baseline.
- CPU throttling stays below 25% and below 1.20x baseline.
- requested CPU and memory remain below 80% of allocatable capacity and below 1.10x baseline.
- memory working set stays below 90% of limits and below 1.10x baseline.
- no new restart, OOMKilled container, or Pending pod appears (both Kubernetes snapshots and
  Prometheus increases are checked so replaced pod names do not hide a burst).
- at least one HPA exists, exposes `currentMetrics`, and reports `ScalingActive=True`.
- load-generator logs are non-empty and the rolling surge completes successfully.

Any `FAIL`, missing series, empty baseline series, or incomplete artifact invalidates the run.
Investigate and repeat with a new run ID; never edit evidence from an official run.
