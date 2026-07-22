# CDO08-SEC-21 PM Apply Runbook

Date: 2026-07-21

This runbook exists because the implementation agent is not allowed to run `kubectl apply`, Argo sync, Helm apply, or Terraform apply for this task.

## Files In This PR

- `argocd/root-resources/techx-production.yaml`
  - Reason: allow Argo CD project `techx-production` to sync `networking.k8s.io/NetworkPolicy`.
- `environments/production/raw/networkpolicies-techx-tf4.yaml`
  - Reason: add default deny and allowlist policies for `techx-tf4`.

## Preflight Read-Only Checks

PM should run these before any apply:

```powershell
kubectl config current-context
kubectl get networkpolicy -n techx-tf4
kubectl get pod -n techx-tf4 | Select-String -Pattern 'orders-mirrormaker2|kafka|checkout|frontend'
kubectl logs -n techx-tf4 orders-mirrormaker2-mirrormaker2-0 --since=2m --tail=80
```

Expected:
- Current context is `arn:aws:eks:us-east-1:511825856493:cluster/techx-tf4-cluster`.
- No `sec21-*` NetworkPolicy is live before rollout.
- MirrorMaker2 health and connector status logs show HTTP `200` and offset commits.

## Server Dry-Run

PM command:

```powershell
kubectl apply --dry-run=server -f argocd\root-resources\techx-production.yaml
kubectl apply --dry-run=server -f environments\production\raw\networkpolicies-techx-tf4.yaml
```

Reason:
- Validate that the live API server accepts the manifests.
- This does not persist resources, but it still uses the `apply` subcommand. The assistant must not run it.

## Apply Order

PM command 1:

```powershell
kubectl apply -f argocd\root-resources\techx-production.yaml
```

Reason:
- AppProject must allow `NetworkPolicy` before Argo can sync the raw manifest.

PM command 2:

```powershell
kubectl apply -f environments\production\raw\networkpolicies-techx-tf4.yaml
```

Reason:
- Roll out the SEC-21 default deny and allowlist policies for `techx-tf4`.

Alternative GitOps path:

```powershell
git push origin cdo08-sec-21-networkpolicy-containment
```

Then merge the PR and let Argo CD sync from `main`.

Reason:
- The `techx-raw` Argo Application watches `environments/production/raw` on `main`.

## Immediate Smoke Checks

PM should run these immediately after apply/sync:

```powershell
kubectl get networkpolicy -n techx-tf4 -l app.kubernetes.io/part-of=cdo08-sec21
kubectl get deploy -n techx-tf4 -o custom-columns=NAME:.metadata.name,READY:.status.readyReplicas,DESIRED:.status.replicas --no-headers
kubectl get rollout -n techx-tf4 cart
kubectl logs -n techx-tf4 orders-mirrormaker2-mirrormaker2-0 --since=2m --tail=80
```

Expected:
- SEC-21 policies exist.
- `sec21-allow-managed-msk-egress` exists and allows only checkout/accounting/fraud-detection to private MSK subnets on TCP/9096.
- All deployments remain Ready.
- Cart rollout remains Available.
- MirrorMaker2 logs continue to show `/health` and connector API `200`, plus offset commits.

## Checkout Smoke Pod

PM can use a temporary hardened pod to run the checkout path. Delete it after logs are captured.

```powershell
kubectl run sec21-checkout-smoke -n techx-tf4 --image=curlimages/curl:8.10.1 --restart=Never --labels='app.kubernetes.io/component=load-generator,cdo08.techx.io/test-role=checkout-smoke' --overrides='{"spec":{"automountServiceAccountToken":false,"securityContext":{"runAsNonRoot":true,"runAsUser":65532,"runAsGroup":65532,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"sec21-checkout-smoke","image":"curlimages/curl:8.10.1","command":["sh","-c","set -eu; user_id=sec21-smoke-$(date +%s); base=http://frontend-proxy:8080; product_id=OLJCESPC7Z; products_code=$(curl -sS --connect-timeout 5 --max-time 15 -o /dev/null -w \"%{http_code}\" \"$base/api/products?currencyCode=USD\"); echo products_http=$products_code; test \"$products_code\" = \"200\"; product_code=$(curl -sS --connect-timeout 5 --max-time 15 -o /dev/null -w \"%{http_code}\" \"$base/api/products/$product_id?currencyCode=USD\"); echo product_http=$product_code; test \"$product_code\" = \"200\"; cart_code=$(curl -sS --connect-timeout 5 --max-time 15 -o /dev/null -w \"%{http_code}\" -H \"Content-Type: application/json\" -X POST \"$base/api/cart\" -d \"{\\\"item\\\":{\\\"productId\\\":\\\"$product_id\\\",\\\"quantity\\\":1},\\\"userId\\\":\\\"$user_id\\\"}\"); echo cart_http=$cart_code; test \"$cart_code\" = \"200\"; checkout_code=$(curl -sS --connect-timeout 5 --max-time 30 -o /dev/null -w \"%{http_code}\" -H \"Content-Type: application/json\" -X POST \"$base/api/checkout\" -d \"{\\\"email\\\":\\\"larry_sergei@example.com\\\",\\\"address\\\":{\\\"streetAddress\\\":\\\"1600 Amphitheatre Parkway\\\",\\\"zipCode\\\":\\\"94043\\\",\\\"city\\\":\\\"Mountain View\\\",\\\"state\\\":\\\"CA\\\",\\\"country\\\":\\\"United States\\\"},\\\"userCurrency\\\":\\\"USD\\\",\\\"creditCard\\\":{\\\"creditCardNumber\\\":\\\"4432-8015-6152-0454\\\",\\\"creditCardExpirationMonth\\\":1,\\\"creditCardExpirationYear\\\":2039,\\\"creditCardCvv\\\":672},\\\"userId\\\":\\\"$user_id\\\"}\"); echo checkout_http=$checkout_code; test \"$checkout_code\" = \"200\""],"resources":{"requests":{"cpu":"25m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"128Mi"}},"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"readOnlyRootFilesystem":true}}]}}'
kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/sec21-checkout-smoke -n techx-tf4 --timeout=180s
kubectl logs -n techx-tf4 sec21-checkout-smoke
kubectl delete pod -n techx-tf4 sec21-checkout-smoke --ignore-not-found=true
```

Expected log:

```text
products_http=200
product_http=200
cart_http=200
checkout_http=200
```

## Rollback

If checkout, Kafka, MirrorMaker2, or readiness regresses, PM should roll back immediately:

```powershell
kubectl delete networkpolicy -n techx-tf4 -l app.kubernetes.io/part-of=cdo08-sec21
kubectl apply -f argocd\root-resources\techx-production.yaml
```

Reason:
- Delete only SEC-21 policies by label.
- Preserve the existing Strimzi-managed MirrorMaker2 policy.
- Re-apply AppProject from the rollback commit if the branch/PR also removes the `NetworkPolicy` whitelist.

Verification after rollback:

```powershell
kubectl get networkpolicy -n techx-tf4
kubectl logs -n techx-tf4 orders-mirrormaker2-mirrormaker2-0 --since=2m --tail=80
kubectl get deploy -n techx-tf4 -o custom-columns=NAME:.metadata.name,READY:.status.readyReplicas,DESIRED:.status.replicas --no-headers
```

Expected:
- Only `orders-mirrormaker2-mirrormaker2` remains in `techx-tf4`.
- MirrorMaker2 logs show HTTP `200` and offset commits again.
- Deployments are Ready.
