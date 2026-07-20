#!/usr/bin/env python3
"""Reject mutable GitHub Action references and Docker base images."""

from __future__ import annotations

import argparse
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
DIGEST_RE = re.compile(r"@sha256:[0-9a-fA-F]{64}$")
USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")
ARG_RE = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)(?:=(.*))?\s*$", re.IGNORECASE)
FROM_RE = re.compile(r"^\s*FROM\s+(.+?)\s*$", re.IGNORECASE)
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
LATEST_RE = re.compile(r"(?:^|[-._:])latest(?:$|[-._])", re.IGNORECASE)


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def workflow_files(root: Path) -> list[Path]:
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return []
    return sorted((*directory.rglob("*.yml"), *directory.rglob("*.yaml")))


def dockerfiles(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("Dockerfile*")
        if ".git" not in path.parts and path.is_file()
    )


def check_actions(root: Path) -> tuple[int, list[Violation]]:
    references = 0
    violations: list[Violation] = []
    for path in workflow_files(root):
        relative = path.relative_to(root)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.lstrip("\ufeff")
            match = USES_RE.match(line)
            if not match:
                continue
            references += 1
            reference = match.group(1).strip("'\"")
            if reference.startswith("./"):
                continue
            if reference.startswith("docker://"):
                image = reference.removeprefix("docker://")
                if not DIGEST_RE.search(image):
                    violations.append(
                        Violation(relative, number, "container action must use a sha256 digest")
                    )
                continue
            if "@" not in reference:
                violations.append(
                    Violation(relative, number, "external action is missing @<full-commit-sha>")
                )
                continue
            _, revision = reference.rsplit("@", 1)
            if not FULL_SHA_RE.fullmatch(revision):
                violations.append(
                    Violation(relative, number, "external action must use a full 40-character commit SHA")
                )
    return references, violations


def expand(value: str, arguments: dict[str, str]) -> str:
    result = value
    for _ in range(10):
        updated = VAR_RE.sub(
            lambda match: arguments.get(match.group(1) or match.group(2), match.group(0)),
            result,
        )
        if updated == result:
            break
        result = updated
    return result.strip("'\"")


def check_docker(root: Path) -> tuple[int, int, list[Violation]]:
    files = dockerfiles(root)
    external_images = 0
    violations: list[Violation] = []
    for path in files:
        relative = path.relative_to(root)
        arguments: dict[str, str] = {}
        stages: set[str] = set()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.lstrip("\ufeff")
            arg_match = ARG_RE.match(line)
            if arg_match:
                arguments[arg_match.group(1)] = (arg_match.group(2) or "").strip().strip("'\"")
                continue
            from_match = FROM_RE.match(line)
            if not from_match:
                continue
            tokens = from_match.group(1).split()
            while tokens and tokens[0].startswith("--"):
                tokens.pop(0)
            if not tokens:
                violations.append(Violation(relative, number, "FROM has no image or stage"))
                continue
            raw_target = tokens[0]
            target = expand(raw_target, arguments)
            if target.lower() in stages:
                pass
            elif target.lower() == "scratch":
                pass
            else:
                external_images += 1
                if "$" in target:
                    violations.append(
                        Violation(relative, number, f"base image variable cannot be resolved: {raw_target}")
                    )
                elif not DIGEST_RE.search(target):
                    violations.append(
                        Violation(relative, number, f"external base image must use a sha256 digest: {target}")
                    )
                else:
                    readable_reference = target.rsplit("@", 1)[0]
                    if LATEST_RE.search(readable_reference.rsplit("/", 1)[-1]):
                        violations.append(
                            Violation(relative, number, f"latest base-image tag is not allowed: {target}")
                        )
            if len(tokens) >= 3 and tokens[-2].lower() == "as":
                stages.add(tokens[-1].lower())
    return len(files), external_images, violations


def scan(root: Path) -> tuple[dict[str, int], list[Violation]]:
    action_count, action_violations = check_actions(root)
    dockerfile_count, image_count, docker_violations = check_docker(root)
    counts = {
        "workflows": len(workflow_files(root)),
        "actions": action_count,
        "dockerfiles": dockerfile_count,
        "external_images": image_count,
    }
    return counts, [*action_violations, *docker_violations]


def write_fixture(root: Path, workflow: str, dockerfile: str = "") -> None:
    workflow_path = root / ".github" / "workflows" / "ci.yaml"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text(workflow, encoding="utf-8")
    if dockerfile:
        docker_path = root / "service" / "Dockerfile"
        docker_path.parent.mkdir(parents=True)
        docker_path.write_text(dockerfile, encoding="utf-8")


def self_test() -> bool:
    sha = "a" * 40
    digest = "b" * 64
    cases = [
        (
            "valid pinned dependencies",
            f"steps:\n  - uses: actions/checkout@{sha} # v4\n  - uses: ./.github/actions/local\n",
            f"FROM python:3.12@sha256:{digest} AS builder\nFROM builder AS final\n",
            0,
        ),
        ("floating action", "steps:\n  - uses: actions/checkout@v4\n", "", 1),
        ("floating image", "steps: []\n", "FROM python:3.12\n", 1),
        ("latest image", "steps: []\n", f"FROM python:latest@sha256:{digest}\n", 1),
        (
            "pinned variable image",
            "steps: []\n",
            f'ARG BASE="python:3.12@sha256:{digest}"\nFROM ${{BASE}} AS final\n',
            0,
        ),
    ]
    passed = True
    for name, workflow, dockerfile, expected in cases:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root, workflow, dockerfile)
            _, violations = scan(root)
            actual = len(violations)
            ok = actual == expected
            print(f"{'PASS' if ok else 'FAIL'} self-test: {name} (violations={actual})")
            passed = passed and ok
    return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test and not self_test():
        return 1

    root = args.root.resolve()
    counts, violations = scan(root)
    print(
        "Pinned dependency scan: "
        f"workflows={counts['workflows']} actions={counts['actions']} "
        f"dockerfiles={counts['dockerfiles']} external_images={counts['external_images']}"
    )
    if violations:
        for violation in violations:
            print(f"ERROR: {violation}")
        print(f"FAIL: {len(violations)} floating or invalid dependency reference(s) found")
        return 1
    print("PASS: all external actions and base images are immutably pinned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
