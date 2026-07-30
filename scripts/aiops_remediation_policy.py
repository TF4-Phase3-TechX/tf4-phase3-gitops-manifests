#!/usr/bin/env python3
"""Fail-closed semantic guard for Mandate 22 bot pull requests."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ".aiops/mandate22-policy.yaml"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
BRANCH = re.compile(r"^aiops/(remediation|compensation)/(inc-[a-z0-9-]{1,48})$")


class GuardFailure(RuntimeError):
    pass


def git(*args: str, text: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=text,
    )
    return result.stdout.strip() if text else result.stdout


def yaml_at(ref: str, path: str) -> dict[str, Any]:
    value = yaml.safe_load(git("show", f"{ref}:{path}"))
    if not isinstance(value, dict):
        raise GuardFailure(f"{path} at {ref} must be a YAML mapping")
    return value


def target(document: dict[str, Any], component: str) -> dict[str, Any]:
    try:
        value = document["components"][component]
    except (KeyError, TypeError) as exc:
        raise GuardFailure(f"missing components.{component}") from exc
    if not isinstance(value, dict):
        raise GuardFailure(f"components.{component} must be a mapping")
    return value


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def env_map(value: dict[str, Any], managed: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in value.get("envOverrides") or []:
        if not isinstance(item, dict):
            raise GuardFailure("envOverrides entries must be mappings")
        if item.get("name") in managed:
            if item["name"] in result:
                raise GuardFailure(f"duplicate managed env {item['name']}")
            result[item["name"]] = item
    return result


def without_allowed(
    value: dict[str, Any], managed: set[str], annotation: str
) -> dict[str, Any]:
    result = copy.deepcopy(value)
    env = [
        item
        for item in result.get("envOverrides") or []
        if item.get("name") not in managed
    ]
    if env:
        result["envOverrides"] = env
    else:
        result.pop("envOverrides", None)
    annotations = dict(result.get("podAnnotations") or {})
    annotations.pop(annotation, None)
    if annotations:
        result["podAnnotations"] = annotations
    else:
        result.pop("podAnnotations", None)
    return result


def parse_metadata(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("{"):
        value = json.loads(raw)
    else:
        matches = re.findall(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if len(matches) != 1:
            raise GuardFailure("PR body must contain exactly one JSON metadata block")
        value = json.loads(matches[0])
    if not isinstance(value, dict):
        raise GuardFailure("PR metadata must be an object")
    return value


def forced_wrong_active(policy: dict[str, Any], incident_id: str) -> bool:
    profile = policy.get("forcedWrongProfile") or {}
    if not profile.get("enabled"):
        return False
    if profile.get("allowedDelta") != "correlation_annotation_only":
        raise GuardFailure("forced-wrong profile has an unsupported delta")
    if profile.get("incidentId") != incident_id:
        raise GuardFailure("forced-wrong profile incident does not match the PR")
    try:
        expiry = datetime.fromisoformat(
            str(profile["expiresAt"]).replace("Z", "+00:00")
        )
    except (KeyError, ValueError) as exc:
        raise GuardFailure("forced-wrong profile expiry is invalid") from exc
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise GuardFailure("forced-wrong profile is expired")
    return True


def demo_actor_allowed(
    policy: dict[str, Any], actor: str, merge_strategy: str
) -> bool:
    profile = policy.get("timeboxedDemoProfile") or {}
    if not profile.get("enabled"):
        return False
    if actor != profile.get("creatorLogin"):
        return False
    if merge_strategy != profile.get("mergeStrategy"):
        return False
    if not profile.get("reviewerLogin") or profile.get("reviewerLogin") == actor:
        raise GuardFailure("time-boxed demo reviewer must be a different account")
    try:
        expiry = datetime.fromisoformat(
            str(profile["expiresAt"]).replace("Z", "+00:00")
        )
    except (KeyError, ValueError) as exc:
        raise GuardFailure("time-boxed demo expiry is invalid") from exc
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise GuardFailure("time-boxed demo profile is expired")
    return True


def changed_files(base: str, head: str) -> list[str]:
    return [
        line for line in git("diff", "--name-only", base, head).splitlines() if line
    ]


def documents(text: str) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in yaml.safe_load_all(text):
        if not item:
            continue
        metadata = item.get("metadata") or {}
        identity = (
            str(item.get("apiVersion", "")),
            str(item.get("kind", "")),
            str(metadata.get("namespace", "")),
            str(metadata.get("name", "")),
        )
        result[identity] = item
    return result


def render(
    chart_dir: Path,
    app_values: Path,
    flagd_values: Path,
    image_values: Path,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    output = subprocess.run(
        [
            "helm",
            "template",
            "techx-corp",
            str(chart_dir),
            "-f",
            str(app_values),
            "-f",
            str(flagd_values),
            "-f",
            str(image_values),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return documents(output)


def rendered_delta_is_bounded(
    base: str, chart_dir: Path, target_file: str, component: str
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        base_app = temporary / "base-app-values.yaml"
        base_app.write_text(git("show", f"{base}:{target_file}"), encoding="utf-8")
        current_app = ROOT / target_file
        flagd = ROOT / "environments/production/flagd-values.yaml"
        images = ROOT / "environments/production/image-revisions.yaml"
        before = render(chart_dir, base_app, flagd, images)
        after = render(chart_dir, current_app, flagd, images)
    if set(before) != set(after):
        raise GuardFailure("rendered resource identities changed")
    changed = [identity for identity in before if before[identity] != after[identity]]
    expected = [
        identity
        for identity in changed
        if identity[1] == "Deployment" and identity[3] == component
    ]
    if changed != expected or len(changed) != 1:
        raise GuardFailure(
            f"rendered delta must contain only Deployment/{component}: {changed}"
        )
    old = copy.deepcopy(before[changed[0]])
    new = copy.deepcopy(after[changed[0]])
    old_template = old.get("spec", {}).pop("template", None)
    new_template = new.get("spec", {}).pop("template", None)
    if old != new or old_template == new_template:
        raise GuardFailure("only the product-reviews pod template may change")


def no_competing_transaction(branch: str) -> None:
    token = os.getenv("GITHUB_TOKEN", "")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    current = int(os.getenv("PR_NUMBER", "0") or 0)
    if not token or not repository:
        return
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/pulls?state=open&per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        pulls = json.load(response)
    competing = [
        item["number"]
        for item in pulls
        if int(item["number"]) != current
        and item.get("head", {}).get("ref", "").startswith("aiops/")
        and item.get("head", {}).get("ref") != branch
    ]
    if competing:
        raise GuardFailure(f"another AIOps target transaction is open: PRs {competing}")


def validate(args: argparse.Namespace) -> None:
    branch_match = BRANCH.fullmatch(args.branch)
    if not branch_match:
        # The required job is safe for ordinary human/CDO pull requests.
        return
    policy = yaml_at(args.base, POLICY_PATH)
    kind, incident_id = branch_match.groups()
    metadata = parse_metadata(args.metadata)
    merge_strategy = str(metadata.get("mergeStrategy", "auto"))
    if args.actor != policy["githubAppLogin"] and not demo_actor_allowed(
        policy, args.actor, merge_strategy
    ):
        raise GuardFailure(
            "AIOps branch author is neither the approved GitOps App nor the "
            "active time-boxed demo creator"
        )
    expected_metadata = {
        "schemaVersion": 2,
        "kind": kind,
        "incidentId": incident_id,
        "component": policy["component"],
        "targetFile": policy["targetFile"],
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            raise GuardFailure(f"metadata {key} does not match policy/branch")
    if changed_files(args.base, args.head) != [policy["targetFile"]]:
        raise GuardFailure("bot PR may change only the policy target file")
    base_sha = git("rev-parse", args.base)
    if metadata.get("baseSha") != base_sha:
        raise GuardFailure("base advanced; bot must recompute the transaction")
    known_good_sha = str(policy["knownGoodCommit"])
    if not FULL_SHA.fullmatch(known_good_sha):
        raise GuardFailure("knownGoodCommit must be a full SHA")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", known_good_sha, base_sha],
        cwd=ROOT,
    ).returncode:
        raise GuardFailure("known-good SHA is not an ancestor of the PR base")
    if metadata.get("knownGoodSha") != known_good_sha:
        raise GuardFailure("PR metadata known-good SHA does not match policy")

    target_file = policy["targetFile"]
    component_name = policy["component"]
    managed = set(policy["managedEnvNames"])
    annotation = policy["correlationAnnotation"]
    base_document = yaml_at(args.base, target_file)
    head_document = yaml_at(args.head, target_file)
    base_target = target(base_document, component_name)
    head_target = target(head_document, component_name)
    if metadata.get("beforeHash") != digest(base_target):
        raise GuardFailure("beforeHash does not match the base target")
    if metadata.get("afterHash") != digest(head_target):
        raise GuardFailure("afterHash does not match the head target")

    base_without_target = copy.deepcopy(base_document)
    head_without_target = copy.deepcopy(head_document)
    base_without_target["components"].pop(component_name)
    head_without_target["components"].pop(component_name)
    if base_without_target != head_without_target:
        raise GuardFailure("service/global values outside product-reviews changed")
    if without_allowed(base_target, managed, annotation) != without_allowed(
        head_target, managed, annotation
    ):
        raise GuardFailure("protected product-reviews fields changed")

    if kind == "remediation":
        if forced_wrong_active(policy, incident_id):
            expected_env = env_map(base_target, managed)
        else:
            known_document = yaml_at(known_good_sha, target_file)
            expected_env = env_map(target(known_document, component_name), managed)
        if env_map(head_target, managed) != expected_env:
            raise GuardFailure("managed env entries do not match known-good SHA")
        if (head_target.get("podAnnotations") or {}).get(annotation) != incident_id:
            raise GuardFailure("correlation annotation does not match incident")
    else:
        # Compensation's exact afterHash is captured before the first action;
        # matching metadata plus the protected-field comparison proves the
        # original target structure is restored without touching other services.
        if (head_target.get("podAnnotations") or {}).get(annotation) == incident_id:
            raise GuardFailure("compensation retained the failed correlation id")

    no_competing_transaction(args.branch)
    if args.chart_dir:
        rendered_delta_is_bounded(
            args.base, Path(args.chart_dir), target_file, component_name
        )


def self_test() -> None:
    managed = {
        "MANDATE22_REVIEW_DELAY_MS",
        "MANDATE22_REVIEW_DELAY_TTL_SECONDS",
        "MANDATE22_REVIEW_DELAY_MAX_REQUESTS",
    }
    annotation = "aiops.techx.io/remediation-id"
    before = {
        "replicas": 2,
        "image": {"tag": "protected"},
        "envOverrides": [
            {"name": "AWS_REGION", "value": "us-east-1"},
            {"name": "MANDATE22_REVIEW_DELAY_MS", "value": "5000"},
        ],
    }
    after = {
        "replicas": 2,
        "image": {"tag": "protected"},
        "envOverrides": [{"name": "AWS_REGION", "value": "us-east-1"}],
        "podAnnotations": {annotation: "inc-self-test"},
    }
    if without_allowed(before, managed, annotation) != without_allowed(
        after, managed, annotation
    ):
        raise GuardFailure("self-test rejected an allowed managed-field delta")
    protected = copy.deepcopy(after)
    protected["image"]["tag"] = "unauthorized"
    if without_allowed(before, managed, annotation) == without_allowed(
        protected, managed, annotation
    ):
        raise GuardFailure("self-test allowed a protected-field edit")
    duplicate = {
        "envOverrides": [
            {"name": "MANDATE22_REVIEW_DELAY_MS", "value": "1"},
            {"name": "MANDATE22_REVIEW_DELAY_MS", "value": "2"},
        ]
    }
    try:
        env_map(duplicate, managed)
    except GuardFailure:
        pass
    else:
        raise GuardFailure("self-test allowed duplicate managed env entries")
    parsed = parse_metadata(
        'text\n```json\n{"schemaVersion": 2, "incidentId": "inc-self-test"}\n```'
    )
    if parsed["schemaVersion"] != 2:
        raise GuardFailure("self-test metadata parser failed")
    profile = {
        "forcedWrongProfile": {
            "enabled": True,
            "incidentId": "inc-self-test",
            "expiresAt": "2099-01-01T00:00:00Z",
            "allowedDelta": "correlation_annotation_only",
        }
    }
    if not forced_wrong_active(profile, "inc-self-test"):
        raise GuardFailure("self-test forced-wrong profile was not recognized")
    profile["forcedWrongProfile"]["expiresAt"] = "2000-01-01T00:00:00Z"
    try:
        forced_wrong_active(profile, "inc-self-test")
    except GuardFailure:
        pass
    else:
        raise GuardFailure("self-test allowed an expired forced-wrong profile")
    demo = {
        "timeboxedDemoProfile": {
            "enabled": True,
            "creatorLogin": "creator",
            "reviewerLogin": "reviewer",
            "mergeStrategy": "dual-token",
            "expiresAt": "2099-01-01T00:00:00Z",
        }
    }
    if not demo_actor_allowed(demo, "creator", "dual-token"):
        raise GuardFailure("self-test time-boxed demo actor was not recognized")
    if demo_actor_allowed(demo, "other", "dual-token"):
        raise GuardFailure("self-test allowed an unlisted demo actor")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--actor", default=os.getenv("PR_ACTOR", ""))
    parser.add_argument("--branch", default=os.getenv("PR_BRANCH", ""))
    parser.add_argument("--metadata", default=os.getenv("PR_BODY", ""))
    parser.add_argument("--chart-dir", default="")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            print("PASS: Mandate 22 policy self-test")
            return 0
        validate(args)
    except (GuardFailure, KeyError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: Mandate 22 semantic and rendered-diff policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
