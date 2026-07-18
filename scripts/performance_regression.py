#!/usr/bin/env python3
"""Collect and evaluate D5-PERF-05 post-enforcement performance evidence.

The runner deliberately keeps raw API responses.  A PASS is never manufactured when a
query, baseline, HPA metric, or Kubernetes check is missing.
"""

import argparse
import datetime as dt
import json
import math
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "environments/production/performance-regression/config.json"


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def run(command, check=True):
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr.strip()}")
    return result


def kubectl_json(namespace, resource, extra=None):
    command = ["kubectl", "-n", namespace, "get", resource, "-o", "json"]
    if extra:
        command[4:4] = extra
    return json.loads(run(command).stdout)


def prom_query(url, query, when):
    params = urllib.parse.urlencode({"query": query, "time": iso(when)})
    request = urllib.request.Request(f"{url.rstrip('/')}/api/v1/query?{params}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def prom_range_query(url, query, start, end, step=30):
    params = urllib.parse.urlencode({"query": query, "start": iso(start), "end": iso(end), "step": step})
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/v1/query_range?{params}", timeout=30) as response:
        return json.load(response)


def values(response):
    if response.get("status") != "success":
        return []
    output = []
    for row in response.get("data", {}).get("result", []):
        try:
            value = float(row["value"][1])
            if math.isfinite(value):
                output.append(value)
        except (KeyError, TypeError, ValueError):
            pass
    return output


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def pod_state(pods):
    pending, restarts, oom = [], 0, []
    for pod in pods.get("items", []):
        name = pod["metadata"]["name"]
        if pod.get("status", {}).get("phase") == "Pending":
            pending.append(name)
        for status in pod.get("status", {}).get("containerStatuses", []):
            restarts += int(status.get("restartCount", 0))
            terminated = status.get("lastState", {}).get("terminated", {})
            current = status.get("state", {}).get("terminated", {})
            if terminated.get("reason") == "OOMKilled" or current.get("reason") == "OOMKilled":
                oom.append(f"{name}/{status.get('name')}")
    return {"pending": pending, "restart_count": restarts, "oom_containers": oom}


def hpa_state(hpas):
    invalid = []
    for hpa in hpas.get("items", []):
        name = hpa["metadata"]["name"]
        current = hpa.get("status", {}).get("currentMetrics", [])
        conditions = {c["type"]: c.get("status") for c in hpa.get("status", {}).get("conditions", [])}
        if not current or conditions.get("ScalingActive") != "True":
            invalid.append(name)
    return {"count": len(hpas.get("items", [])), "invalid": invalid}


def snapshot(out, cfg, label, when):
    raw = out / "raw" / label
    raw.mkdir(parents=True, exist_ok=True)
    pods = kubectl_json(cfg["namespace"], "pods")
    hpas = kubectl_json(cfg["namespace"], "hpa")
    nodes = json.loads(run(["kubectl", "get", "nodes", "-o", "json"]).stdout)
    atomic_json(raw / "pods.json", pods)
    atomic_json(raw / "hpa.json", hpas)
    atomic_json(raw / "nodes.json", nodes)
    metrics = {}
    for name, spec in cfg["queries"].items():
        response = prom_query(cfg["prometheus_url"], spec["promql"], when)
        atomic_json(raw / f"prometheus-{name}.json", response)
        metrics[name] = values(response)
    return {"time": iso(when), "pods": pod_state(pods), "hpa": hpa_state(hpas), "metrics": metrics}


def rollout_surge(out, cfg):
    deployment = cfg.get("surge_deployment")
    if not deployment:
        return {"executed": False, "reason": "surge_deployment is not configured"}
    before = kubectl_json(cfg["namespace"], f"deployment/{deployment}")
    strategy = before.get("spec", {}).get("strategy", {}).get("rollingUpdate", {})
    run(["kubectl", "-n", cfg["namespace"], "rollout", "restart", f"deployment/{deployment}"])
    status = run(["kubectl", "-n", cfg["namespace"], "rollout", "status", f"deployment/{deployment}",
                  f"--timeout={cfg.get('rollout_timeout_seconds', 300)}s"], check=False)
    after_pods = kubectl_json(cfg["namespace"], "pods")
    atomic_json(out / "raw" / "surge" / "deployment-before.json", before)
    atomic_json(out / "raw" / "surge" / "pods-after.json", after_pods)
    (out / "raw" / "surge" / "rollout-status.txt").write_text(status.stdout + status.stderr, encoding="utf-8")
    return {"executed": True, "deployment": deployment, "maxSurge": strategy.get("maxSurge"),
            "rollout_exit_code": status.returncode, "pending_after": pod_state(after_pods)["pending"]}


def evaluate(cfg, baseline, start, end, surge, raw_load_ok):
    checks = {}
    for name, spec in cfg["queries"].items():
        current_values = end["metrics"].get(name, [])
        baseline_values = baseline.get("metrics", {}).get(name, [])
        current = max(current_values) if current_values else None
        prior = max(baseline_values) if baseline_values else None
        ok = current is not None and prior is not None
        reasons = []
        if not ok:
            reasons.append("missing current or baseline series")
        if ok and "absolute_max" in spec and current > spec["absolute_max"]:
            ok, reasons = False, reasons + [f"{current} > absolute_max {spec['absolute_max']}"]
        if ok and "max_regression_ratio" in spec and prior > 0 and current > prior * spec["max_regression_ratio"]:
            ok, reasons = False, reasons + [f"{current} > baseline {prior} x {spec['max_regression_ratio']}"]
        if ok and "max_regression_ratio" in spec and prior == 0 and current > 0:
            ok, reasons = False, reasons + [f"{current} regressed from a zero baseline"]
        checks[name] = {"pass": ok, "current": current, "baseline": prior, "reasons": reasons}
    checks["no_pending_pods"] = {"pass": not end["pods"]["pending"], "value": end["pods"]["pending"]}
    restart_delta = end["pods"]["restart_count"] - start["pods"]["restart_count"]
    checks["no_restart_or_oom_burst"] = {"pass": restart_delta <= cfg["max_restart_delta"] and not end["pods"]["oom_containers"],
                                           "restart_delta": restart_delta, "oom": end["pods"]["oom_containers"]}
    checks["hpa_valid_metrics"] = {"pass": end["hpa"]["count"] > 0 and not end["hpa"]["invalid"], **end["hpa"]}
    checks["rolling_surge"] = {"pass": surge.get("executed") and surge.get("rollout_exit_code") == 0 and
                               surge.get("maxSurge") not in (None, 0, "0", "0%") and not surge.get("pending_after"), **surge}
    checks["raw_load_artifact"] = {"pass": raw_load_ok}
    return {"result": "PASS" if all(c["pass"] for c in checks.values()) else "FAIL", "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--baseline", type=Path, required=True, help="summary.json from the pre-remediation run")
    parser.add_argument("--duration-seconds", type=int, default=600)
    parser.add_argument("--execute-surge-restart", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", args.run_id):
        raise SystemExit("run ID must be 3-80 safe filename characters")
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    if args.duration_seconds < 300:
        raise SystemExit("controlled window must be at least 300 seconds")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    out = ROOT / "docs/evidence/directive-05" / f"official-{args.run_id}" / "performance-regression"
    if out.exists():
        raise SystemExit(f"refusing to overwrite evidence directory: {out}")
    out.mkdir(parents=True)
    metadata = {"run_id": args.run_id, "git_sha": run(["git", "rev-parse", "HEAD"]).stdout.strip(),
                "config": str(args.config.relative_to(ROOT)), "baseline": str(args.baseline),
                "duration_seconds": args.duration_seconds}
    start_time = utc_now()
    start = snapshot(out, cfg, "start", start_time)
    # Existing load-generator must already be running its reviewed, fixed profile.
    time.sleep(args.duration_seconds)
    end_time = utc_now()
    end = snapshot(out, cfg, "end", end_time)
    for name, spec in cfg["queries"].items():
        response = prom_range_query(cfg["prometheus_url"], spec["promql"], start_time, end_time)
        atomic_json(out / "raw" / "same-window" / f"prometheus-{name}.json", response)
    logs = run(["kubectl", "-n", cfg["namespace"], "logs", "-l", cfg["load_generator_selector"],
                f"--since-time={iso(start_time)}", "--all-containers=true", "--prefix=true"], check=False)
    (out / "raw" / "load-generator.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")
    surge = rollout_surge(out, cfg) if args.execute_surge_restart else {"executed": False, "reason": "flag not supplied"}
    report = evaluate(cfg, baseline, start, end, surge, logs.returncode == 0 and bool(logs.stdout.strip()))
    summary = {**metadata, "window": {"start": iso(start_time), "end": iso(end_time)},
               "metrics": end["metrics"], "start": start, "end": end, "surge": surge, **report}
    atomic_json(out / "summary.json", summary)
    (out / "RESULT").write_text(report["result"] + "\n", encoding="ascii")
    print(out)
    print(report["result"])
    raise SystemExit(0 if report["result"] == "PASS" else 2)


if __name__ == "__main__":
    main()
