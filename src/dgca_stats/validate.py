"""Cross-run checks and coverage reporting."""
import pandas as pd

from .parse.registry import dataset_for


def coverage(df: pd.DataFrame) -> str:
    known = df[df.apply(lambda r: dataset_for(r["title"], r["ext"], r["portal_path"]) is not None, axis=1)]
    if known.empty:
        return "no parseable files in manifest"
    t = known.pivot_table(index="fy", columns="parse_status", values="s3_url", aggfunc="count", fill_value=0)
    t["files"] = t.sum(axis=1)
    miss = known[known.status == "missing"].groupby("fy").size().rename("missing_at_source")
    return t.join(miss).fillna(0).astype(int).to_string()


def report(df: pd.DataFrame) -> int:
    print(coverage(df))
    known = df[df.apply(lambda r: dataset_for(r["title"], r["ext"], r["portal_path"]) is not None, axis=1)]
    bad = known[known.parse_status == "quarantined"]
    pending = known[(known.status == "downloaded") & (known.parse_status == "pending")]
    import yaml

    from . import config as _c
    accepted = [k["file"] for k in yaml.safe_load((_c.ROOT / "semantic" / "semantic.yaml").read_text()).get("known_issues", []) if k.get("file")]
    is_known = bad.portal_path.map(lambda p: any(str(p).endswith(a) for a in accepted))
    for _, r in bad[is_known].iterrows():
        print(f"known issue (accepted): {str(r.portal_path).rsplit('/', 1)[-1]}")
    bad = bad[~is_known]
    for _, r in bad.iterrows():
        print(f"QUARANTINED {r.fy} {r.entity} {r.table_id}: {r.error}\n  -> make ai-fix-parser FILE=data/quarantine/{r.content_sha256}.{r.ext}")
    if len(pending):
        print(f"{len(pending)} downloaded files not parsed yet (run `dgca parse`)")
    rc = 1 if len(bad) else 0
    import json

    from . import config
    latest = config.SITE_DATA / "latest.json"
    if latest.exists():
        cat = json.loads((config.SITE_DATA / json.loads(latest.read_text())["version"] / "catalog.json").read_text())
        for c in cat.get("checks", []):
            print(f"check {c['name']}: {c['within_tolerance']}/{c['months_compared']} months within {c['tolerance']:.0%}, median ratio {c['median_ratio']}, "
                  f"{len(c['deviations'])} deviations ({c['unexplained']} unexplained)")
            for d in c["deviations"]:
                if not d["explained"]:
                    print(f"  UNEXPLAINED {d['period_key']}: ratio {d['ratio']}")
                    rc = 1
    return rc
