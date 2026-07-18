#!/usr/bin/env python3
"""Promote exactly one D5-PERF-03 resource wave into the active GitOps values."""

import argparse
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLLOUT = ROOT / "environments/production/resource-rollout"
APP_VALUES = ROOT / "environments/production/app-values.yaml"
OBS_VALUES = ROOT / "environments/production/observability-values.yaml"


def merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value


def require_controlled_window():
    missing = [name for name in ("RUN_ID", "CHANGE_TICKET", "WINDOW_START_UTC", "WINDOW_END_UTC") if not os.getenv(name)]
    if missing:
        raise SystemExit("missing controlled-window metadata: " + ", ".join(missing))


def promote(wave):
    require_controlled_window()
    matches = list(ROLLOUT.glob(f"wave-{wave}-*.yaml"))
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one definition for wave {wave}")
    if wave != "01":
        prior = f"{int(wave) - 1:02d}"
        result = ROOT / "docs/evidence/directive-05" / f"official-{os.environ['RUN_ID']}" / "resource-rollout" / f"wave-{prior}" / "RESULT"
        if not result.exists() or result.read_text().strip() != "PASS":
            raise SystemExit(f"wave {prior} must have RESULT=PASS before promoting wave {wave}")

    target = OBS_VALUES if wave == "04" else APP_VALUES
    base = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    override = yaml.safe_load(matches[0].read_text(encoding="utf-8")) or {}
    merge(base, override)
    target.write_text(yaml.safe_dump(base, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"promoted {matches[0].name} into {target.relative_to(ROOT)}")
    print("review the diff, commit this wave alone, then allow Argo CD to sync")


def rollback_command(wave):
    target = OBS_VALUES if wave == "04" else APP_VALUES
    print(f"git revert <wave-{wave}-commit>  # restores {target.relative_to(ROOT)}; merge and let Argo CD sync")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    promote_parser = sub.add_parser("promote")
    promote_parser.add_argument("wave", choices=["01", "02", "03", "04", "05"])
    rollback_parser = sub.add_parser("rollback-command")
    rollback_parser.add_argument("wave", choices=["01", "02", "03", "04", "05"])
    args = parser.parse_args()
    if args.command == "promote":
        promote(args.wave)
    else:
        rollback_command(args.wave)


if __name__ == "__main__":
    main()
