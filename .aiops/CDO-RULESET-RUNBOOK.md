# Mandate 22 CDO ruleset bootstrap

Owner: CDO/platform. The AIOps implementation owner prepares this artifact but
does not apply repository-administration changes.

The script is dry-run by default and manages only:

- repository auto-merge enabled;
- `aiops-required-checks`, with no bypass actors;
- `aiops-pr-review`, with one normal approval and the CDO-owned GitOps App using
  `bypass_mode=pull_request`.

It never deletes or changes unrelated rulesets.

```bash
python scripts/bootstrap_aiops_rulesets.py --self-test

export GH_TOKEN='<repository-administration token>'
python scripts/bootstrap_aiops_rulesets.py --audit
python scripts/bootstrap_aiops_rulesets.py --apply
python scripts/bootstrap_aiops_rulesets.py --audit
```

The first audit exits `2` when drift exists. The final audit must exit `0` and
report every managed item as `unchanged`. Save the sanitized JSON output and
ruleset URLs with the CDO sign-off. Never commit either token or the App private
key.
