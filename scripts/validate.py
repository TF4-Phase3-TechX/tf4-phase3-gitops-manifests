#!/usr/bin/env python3
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS = ROOT / "argocd/root-resources/applications.yaml"
DESTRUCTIVE_KINDS = {"PersistentVolumeClaim", "PersistentVolume", "Service", "Namespace", "CustomResourceDefinition"}
SECRET_PATTERN = re.compile(r"(?i)(password|token|secret|api[-_]?key)\s*:\s*['\"]?[^${\s][^\n]*")
# Lines that match SECRET_PATTERN but are structural ExternalSecret fields, not plaintext values.
# These are safe: they reference remote key paths or output field names, never actual secret material.
_ESO_SAFE_LINE = re.compile(
    r"^\s*("
    r"#"                                        # comment
    r"|secretKey\s*:"                           # ESO output key name
    r"|(remoteRef\s*:\s*$)"                     # remoteRef block opener
    r"|key\s*:\s*\S"                            # remoteRef.key  (AWS SM path)
    r"|property\s*:\s*\S"                       # remoteRef.property
    r"|(target\s*:\s*$)"                        # target block opener
    r"|name\s*:\s*\S"                           # target.name / metadata.name
    r"|creationPolicy\s*:"                      # target.creationPolicy
    r")"
)


def documents(path):
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def has_plaintext_credential(text: str) -> bool:
    """Return True if any line looks like a plaintext credential.

    Lines that are structural ExternalSecret fields (secretKey, remoteRef.key,
    remoteRef.property, target.name, comments) are skipped before matching, so
    ESO manifests with safe path references do not false-positive.
    """
    for line in text.splitlines():
        if _ESO_SAFE_LINE.match(line):
            continue
        if SECRET_PATTERN.search(line):
            return True
    return False


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
        if has_plaintext_credential(path.read_text()):
            raise SystemExit(f"possible plaintext credential in {path.relative_to(ROOT)}")

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
