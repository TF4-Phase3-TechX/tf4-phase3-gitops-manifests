#!/usr/bin/env python3
"""Plan, audit or idempotently apply the Mandate 22 GitHub rulesets.

Dry-run is the default. Applying requires a repository-administration token and
the numeric GitHub App integration id; the script never deletes other rulesets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".aiops" / "cdo-bootstrap.yaml"
API = "https://api.github.com"
EXPECTED_CHECKS = [
    "validate",
    "check-pinned-dependencies",
    "aiops-remediation-policy",
]


class BootstrapError(RuntimeError):
    pass


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise BootstrapError("CDO bootstrap config must be a YAML mapping")
    repositories = config.get("githubApp", {}).get("repositorySelection") or []
    if repositories != ["TF4-Phase3-TechX/tf4-phase3-gitops-manifests"]:
        raise BootstrapError("GitHub App must be scoped to the one GitOps repository")
    app = config.get("githubApp", {})
    if (
        app.get("slug") != "gitops-promotion-bot-tf4"
        or app.get("actorId") != 4301466
        or app.get("reusePolicy") != "cdo_owned_gitops_automation_only"
    ):
        raise BootstrapError("approved CDO GitOps App identity drifted")
    if app.get("permissions") != {
        "metadata": "read",
        "contents": "write",
        "pullRequests": "write",
        "checks": "read",
    }:
        raise BootstrapError("approved GitOps App permissions drifted")
    if app.get("explicitlyDenied") != [
        "administration",
        "actions",
        "secrets",
    ]:
        raise BootstrapError("GitHub App denied permissions drifted")
    if config.get("repositorySettings") != {"allowAutoMerge": True}:
        raise BootstrapError("repository auto-merge must remain enabled")
    return config


def ruleset_payloads(
    config: dict[str, Any],
    *,
    app_actor_id: int,
) -> dict[str, dict[str, Any]]:
    rulesets = {item["name"]: item for item in config.get("rulesets") or []}
    if set(rulesets) != {"aiops-required-checks", "aiops-pr-review"}:
        raise BootstrapError("exactly the two protected AIOps rulesets are required")

    checks = rulesets["aiops-required-checks"]
    if checks.get("requiredChecks") != EXPECTED_CHECKS:
        raise BootstrapError("required checks must match the Mandate 22 policy")
    if checks.get("githubAppBypass") is not False:
        raise BootstrapError("the bot must not bypass required checks")

    review = rulesets["aiops-pr-review"]
    bypass = review.get("reviewBypass") or {}
    if bypass != {
        "githubApp": "gitops-promotion-bot-tf4",
        "scope": "pull_request_only",
    }:
        raise BootstrapError("review bypass must be PR-only for the CDO GitOps App")
    if review.get("requiredApprovals") != 1:
        raise BootstrapError("ordinary pull requests must retain one approval")

    conditions = {
        "ref_name": {
            "exclude": [],
            "include": ["refs/heads/main"],
        }
    }
    return {
        "aiops-required-checks": {
            "name": "aiops-required-checks",
            "target": "branch",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": conditions,
            "rules": [
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "do_not_enforce_on_create": False,
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [
                            {"context": name} for name in EXPECTED_CHECKS
                        ],
                    },
                }
            ],
        },
        "aiops-pr-review": {
            "name": "aiops-pr-review",
            "target": "branch",
            "enforcement": "active",
            "bypass_actors": [
                {
                    "actor_id": app_actor_id,
                    "actor_type": "Integration",
                    "bypass_mode": "pull_request",
                }
            ],
            "conditions": conditions,
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {
                    "type": "pull_request",
                    "parameters": {
                        "allowed_merge_methods": ["squash"],
                        "dismiss_stale_reviews_on_push": bool(
                            review["dismissStaleReviewsOnPush"]
                        ),
                        "require_code_owner_review": False,
                        "require_last_push_approval": False,
                        "required_approving_review_count": 1,
                        "required_review_thread_resolution": bool(
                            review["requireReviewThreadResolution"]
                        ),
                    },
                },
            ],
        },
    }


class GitHub:
    def __init__(self, repository: str, token: str):
        self.repository = repository
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{API}/repos/{self.repository}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise BootstrapError(
                f"GitHub {method} {path} failed ({exc.code}): {detail}"
            ) from exc
        return json.loads(body) if body else None

    def app(self, slug: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{API}/apps/{slug}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise BootstrapError(
                f"GitHub GET /apps/{slug} failed ({exc.code}): {detail}"
            ) from exc


def normalized(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "name",
            "target",
            "enforcement",
            "bypass_actors",
            "conditions",
            "rules",
        )
    }


def audit_or_apply(
    github: GitHub,
    payloads: dict[str, dict[str, Any]],
    *,
    app_slug: str,
    apply: bool,
) -> list[dict[str, Any]]:
    app = github.app(app_slug)
    expected_permissions = {
        "metadata": "read",
        "contents": "write",
        "pull_requests": "write",
        "checks": "read",
    }
    app_permissions_match = all(
        app.get("permissions", {}).get(name) == level
        for name, level in expected_permissions.items()
    )
    if apply and not app_permissions_match:
        raise BootstrapError(
            "GitHub App permissions must include Checks read before ruleset apply"
        )
    repository = github.request("GET", "")
    auto_merge_enabled = repository.get("allow_auto_merge") is True
    results: list[dict[str, Any]] = [
        {
            "name": "github-app-permissions",
            "action": "unchanged" if app_permissions_match else "add-checks-read",
            "applied": False,
        },
        {
            "name": "repository-auto-merge",
            "action": "unchanged" if auto_merge_enabled else "enable",
            "applied": apply,
        },
    ]
    if apply and not auto_merge_enabled:
        github.request("PATCH", "", {"allow_auto_merge": True})

    existing = {
        item["name"]: item
        for item in github.request("GET", "/rulesets")
        if item["name"] in payloads and item.get("source_type") == "Repository"
    }
    for name, desired in payloads.items():
        current_summary = existing.get(name)
        current = (
            github.request("GET", f"/rulesets/{current_summary['id']}")
            if current_summary
            else None
        )
        matches = current is not None and normalized(current) == normalized(desired)
        action = "unchanged" if matches else "update" if current else "create"
        if apply and action != "unchanged":
            if current:
                github.request("PUT", f"/rulesets/{current['id']}", desired)
            else:
                github.request("POST", "/rulesets", desired)
        results.append({"name": name, "action": action, "applied": apply})
    return results


def self_test() -> None:
    config = load_config()
    payloads = ruleset_payloads(config, app_actor_id=12345)
    checks = payloads["aiops-required-checks"]
    review = payloads["aiops-pr-review"]
    assert checks["bypass_actors"] == []
    assert [
        item["context"]
        for item in checks["rules"][0]["parameters"]["required_status_checks"]
    ] == EXPECTED_CHECKS
    assert review["bypass_actors"] == [
        {
            "actor_id": 12345,
            "actor_type": "Integration",
            "bypass_mode": "pull_request",
        }
    ]
    assert any(item["type"] == "pull_request" for item in review["rules"])

    invalid_checks = json.loads(json.dumps(config))
    invalid_checks["rulesets"][0]["requiredChecks"].pop()
    try:
        ruleset_payloads(invalid_checks, app_actor_id=12345)
    except BootstrapError:
        pass
    else:
        raise BootstrapError("self-test allowed a missing required check")

    invalid_bypass = json.loads(json.dumps(config))
    invalid_bypass["rulesets"][0]["githubAppBypass"] = True
    try:
        ruleset_payloads(invalid_bypass, app_actor_id=12345)
    except BootstrapError:
        pass
    else:
        raise BootstrapError("self-test allowed required-check bypass")

    invalid_scope = json.loads(json.dumps(config))
    invalid_scope["rulesets"][1]["reviewBypass"]["scope"] = "always"
    try:
        ruleset_payloads(invalid_scope, app_actor_id=12345)
    except BootstrapError:
        pass
    else:
        raise BootstrapError("self-test allowed a non-PR bypass")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--app-actor-id", type=int, default=0)
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            print("PASS: AIOps ruleset bootstrap contract")
            return 0
        config = load_config()
        actor_id = args.app_actor_id or int(config["githubApp"]["actorId"])
        payloads = ruleset_payloads(config, app_actor_id=actor_id)
        repository = config["githubApp"]["repositorySelection"][0]
        if not args.audit and not args.apply:
            print(json.dumps(payloads, indent=2, sort_keys=True))
            return 0
        token = os.getenv("GH_TOKEN", "")
        if not token:
            raise BootstrapError("GH_TOKEN is required for audit/apply")
        if args.apply and actor_id <= 0:
            raise BootstrapError("a positive GitHub App actor id is required")
        results = audit_or_apply(
            GitHub(repository, token),
            payloads,
            app_slug=config["githubApp"]["slug"],
            apply=args.apply,
        )
        print(json.dumps(results, indent=2))
        if args.audit and any(item["action"] != "unchanged" for item in results):
            return 2
        return 0
    except (BootstrapError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
