# D13-REL-01 — Spot Interruption Tabletop Report

## Report status

```text
REPORT_TYPE=TABLETOP_SIMULATION
LIVE_SPOT_TERMINATION_EXECUTED=NO
VERDICT=NOT_EXECUTED_PRE_DRILL_GATE_FAILED
```

This document closes the historical reporting gap for D13-REL-01 without
representing simulated values as production evidence. No EC2 instance,
Kubernetes Node, or Karpenter NodeClaim was terminated as part of this report.

The live drill acceptance criteria therefore remain **not evaluated**. A future
live drill must produce a separate, timestamped evidence bundle.

## Objective

Validate that terminating a Spot worker while it is serving Browse, Cart, and
Checkout traffic does not create customer-visible errors, and that pods and
capacity recover automatically.

## Observed pre-drill snapshot

The following values were observed read-only from Kubernetes and Locust on
2026-07-28. They are preflight evidence only, not interruption results.

```text
OBSERVATION_TIMESTAMP_UTC=2026-07-28T16:23:22Z
SPOT_INSTANCE_ID=i-0f6b28fa988d70036
KUBERNETES_NODE_NAME=ip-10-0-10-115.ec2.internal
NODECLAIM_NAME=techx-arm64-spot-jr4cd
INSTANCE_TYPE=r7g.large
ARCHITECTURE=arm64
CAPACITY_TYPE=spot
NODE_READY=True
INTERRUPTION_TIMESTAMP_UTC=NOT_APPLICABLE

LOCUST_STATE=running
ACTIVE_USERS=200
CURRENT_RPS=55.7
CURRENT_FAILURES_PER_SECOND=2.3
ACCUMULATED_FAIL_RATIO=0.0049488704236075555
```

The candidate node hosted 22 pods, including the following application
components:

```text
ad
cart
checkout
currency
email
frontend
frontend-proxy
image-provider
llm
payment
product-catalog
product-reviews
quote
recommendation
shipping
```

This proves that a real Spot/ARM64 node was Ready and serving in-scope
workloads under a 200-user load. It does not prove interruption recovery.

## Why the live action was stopped

The pre-drill SLO gate was already red:

- Locust reported non-zero failures before any interruption.
- Existing errors included HTTP 500, 503, and 504 responses.
- Browse-adjacent `product-reviews` requests experienced timeouts.
- Kubernetes reported a `product-reviews` readiness probe timeout.
- The available Kubernetes identity could not delete or patch NodeClaims or
  delete Nodes.
- The local AWS CLI session had no credentials for an approved EC2 termination.

Terminating the node in this state would not allow errors to be attributed to
the interruption and would violate the hard-stop conditions of Directive #13.

## Acceptance assessment

| Criterion | Result | Evidence |
| --- | --- | --- |
| Spot node was actually terminated | Not evaluated | No termination performed |
| Traffic passed through workloads on the node | Confirmed preflight | 200 users, 55.7 RPS; in-scope pods placed on candidate |
| Customer request denominator > 0 | Confirmed preflight | Locust active with non-zero RPS |
| Customer error count = 0 | Gate failed | Failures existed before the drill |
| Browse failures = 0 | Gate failed | Pre-existing Browse-adjacent failures/timeouts |
| Cart failures = 0 | Gate failed | Pre-existing Cart failures |
| Checkout failures = 0 | Gate failed | Pre-existing Checkout failures |
| Storefront p95 < 1 second | Not evaluated for a clean drill window | No isolated interruption window |
| Pods rescheduled successfully | Not evaluated | No termination performed |
| Replacement capacity became Ready | Not evaluated | No replacement was requested |
| Cluster returned to healthy | Not evaluated | No interruption lifecycle occurred |

## Tabletop simulation

The following sequence is an **illustrative expected flow**, not observed data
and not acceptable as production evidence:

1. Confirm a clean five-minute pre-drill window with zero customer errors.
2. Record the Spot instance, Node, NodeClaim, hosted pods, and baseline SLOs.
3. An approved interruption operator terminates the EC2 Spot instance.
4. The Node becomes `NotReady`; termination handling drains or evicts pods
   while PDBs preserve serving capacity.
5. Deployments reschedule replicas across healthy nodes.
6. Karpenter creates replacement capacity when existing allocatable capacity is
   insufficient.
7. The replacement NodeClaim and Node become Ready.
8. Observe a minimum five-minute recovery window and save raw Locust,
   Kubernetes, Karpenter, EC2, and Grafana evidence.

Expected acceptance values for that future run are:

```text
CUSTOMER_REQUEST_DENOMINATOR_GT_ZERO=true
CUSTOMER_ERROR_COUNT=0
BROWSE_FAILURES=0
CART_FAILURES=0
CHECKOUT_FAILURES=0
HTTP_5XX=0
TIMEOUTS=0
CONNECTION_RESETS=0
LOCUST_EXCEPTIONS=0
STOREFRONT_P95_MS_LT_1000=true
POD_RESCHEDULE_SUCCESS=true
REPLACEMENT_CAPACITY_READY=true
CLUSTER_HEALTHY=true
```

These are targets, not measured results.

## Evidence required to upgrade this report to a live PASS

- Approved change window and interruption operator.
- A clean pre-drill SLO window.
- EC2 lifecycle and termination timestamps.
- Kubernetes Node and NodeClaim lifecycle.
- Pod eviction, PDB, and rescheduling events.
- Replacement NodeClaim launch and Ready timestamps.
- HPA timeline.
- Raw Locust requests, failures, exceptions, and request denominator.
- Browse, Cart, Checkout, HTTP 5xx, timeout, and connection-reset counts.
- Storefront p95 across pre-interruption, interruption, and recovery windows.
- Final node, pod, and application health snapshot.

Until that bundle exists, the authoritative result remains:

```text
NOT EXECUTED — PRE-DRILL SLO AND PERMISSION GATES FAILED
```
