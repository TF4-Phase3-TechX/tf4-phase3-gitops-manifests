#!/usr/bin/env python3
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS = ROOT / "argocd/root-resources/applications.yaml"
DESTRUCTIVE_KINDS = {"PersistentVolumeClaim", "PersistentVolume", "Service", "Namespace", "CustomResourceDefinition"}
SECRET_PATTERN = re.compile(r"(?i)(password|token|secret|api[-_]?key)\s*:\s*['\"]?[^${\s][^\n]*")
EXPECTED_WAVES = {
    "wave-01-low-risk-stateless.yaml": {"ad", "email", "image-provider", "quote"},
    "wave-02-revenue-critical-stateless.yaml": {"frontend", "frontend-proxy", "cart", "checkout", "currency", "payment", "product-catalog", "shipping"},
    "wave-03-stateful-messaging.yaml": {"accounting", "fraud-detection", "kafka", "postgresql", "valkey-cart"},
    "wave-05-exceptions.yaml": {"flagd", "llm", "load-generator", "product-reviews", "recommendation"},
}


def documents(path):
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def main():
    apps = documents(APPLICATIONS)
    chart_revisions = [
        source["targetRevision"]
        for app in apps
        for source in app.get("spec", {}).get("sources", [])
        if source.get("repoURL") == "https://github.com/TF4-Phase3-TechX/tf4-phase3-repo.git"
    ]
    if len(set(chart_revisions)) != 1 or not re.fullmatch(r"[0-9a-f]{40}", chart_revisions[0]):
        raise SystemExit("Applications must use one full immutable source chart SHA")

    seen = set()
    for path in ROOT.rglob("*.yaml"):
        if ".git" in path.parts:
            continue
        for doc in documents(path):
            meta = doc.get("metadata", {})
            kind, name = doc.get("kind"), meta.get("name")
            namespace = meta.get("namespace", "")
            if kind and name:
                identity = (doc.get("apiVersion", "v1"), kind, namespace, name)
                if identity in seen:
                    raise SystemExit(f"duplicate manifest identity: {identity}")
                seen.add(identity)
        if SECRET_PATTERN.search(path.read_text()) and "all-secrets.yaml" not in str(path):
            raise SystemExit(f"possible plaintext credential in {path.relative_to(ROOT)}")

    rollout = ROOT / "environments/production/resource-rollout"
    for filename, expected in EXPECTED_WAVES.items():
        values = yaml.safe_load((rollout / filename).read_text())
        actual = set(values.get("components", {}))
        if actual != expected:
            raise SystemExit(f"incorrect workload membership in {filename}: {sorted(actual)}")
        for component, config in values["components"].items():
            resources = config.get("resources", {})
            if set(resources) != {"requests", "limits"}:
                raise SystemExit(f"incomplete resources for {component} in {filename}")
    observability = yaml.safe_load((rollout / "wave-04-observability.yaml").read_text())
    expected_observability = {"opentelemetry-collector", "prometheus", "grafana", "opensearch"}
    if set(observability) != expected_observability:
        raise SystemExit("incorrect workload membership in wave-04-observability.yaml")

    perf_config = json.loads((ROOT / "environments/production/performance-regression/config.json").read_text())
    required_queries = {"storefront_p95_seconds", "browse_error_ratio", "cart_error_ratio",
                        "checkout_error_ratio", "cpu_throttling_ratio",
                        "memory_working_set_ratio", "container_restart_increase", "oom_killed_increase",
                        "scheduler_requested_cpu_ratio", "scheduler_requested_memory_ratio"}
    if set(perf_config.get("queries", {})) != required_queries:
        raise SystemExit("D5-PERF-05 query set is incomplete")

    base = subprocess.run(["git", "merge-base", "origin/main", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    changed = subprocess.run(["git", "diff", "--name-status", base, "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.splitlines()
    deleted = [line for line in changed if line.startswith("D\t") and Path(line.split("\t", 1)[1]).suffix in {".yaml", ".yml"}]
    if deleted:
        deleted_docs = []
        for line in deleted:
            old = subprocess.run(["git", "show", f"{base}:{line.split(chr(9), 1)[1]}"], cwd=ROOT, text=True, capture_output=True)
            if old.returncode == 0:
                deleted_docs.extend(documents_from_text(old.stdout))
        if any(doc.get("kind") in DESTRUCTIVE_KINDS for doc in deleted_docs):
            raise SystemExit("destructive manifest deletion requires a separate reviewed override")


def documents_from_text(text):
    return [doc for doc in yaml.safe_load_all(text) if doc]


if __name__ == "__main__":
    main()
