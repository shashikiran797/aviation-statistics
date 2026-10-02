"""`dgca` command line: every step is idempotent, so `dgca update` is safe to re-run (locally or in CI)."""
import argparse
import sys

from . import config, http, manifest
from .crawl import discover as disc
from .crawl import download as dl
from .parse import run as parse_run
from .parse.registry import dataset_for


def cmd_crawl(a):
    sess = http.session()
    df = manifest.load()
    years = set(a.years.split(",")) if a.years else None
    recs, unsupported = disc.discover(sess, refresh=a.refresh, years=years)
    df, st = manifest.upsert(df, recs)
    manifest.save(df)
    print(f"crawl: {len(recs)} files found, {st['new']} new, {st['seen_again']} already known; "
          f"{len(unsupported)} pages with unsupported layout")
    for u in unsupported:
        print(f"  unsupported: {u.get('fy')} {u['portal_path'].rsplit('/', 1)[-1]} ({u['reason']})")


def cmd_download(a):
    df = manifest.load()
    known = df.apply(lambda r: dataset_for(r["title"], r["ext"]) is not None, axis=1)
    mask = known & df["fy"].isin(a.years.split(",")) if a.years else known
    if a.retry_missing is False:
        mask &= df["status"] != "missing"
    dl.download(http.session(), df, mask, force=a.force)
    manifest.save(df)


def cmd_parse(a):
    df = manifest.load()
    parse_run.run(df, only_pending=not a.all)
    manifest.save(df)


def cmd_validate(a):
    from .validate import report
    sys.exit(report(manifest.load()))


def cmd_publish(a):
    from .publish.build import build
    build(manifest.load())


def cmd_status(a):
    from .validate import coverage
    print(coverage(manifest.load()))


def cmd_site_crawl(a):
    """Walk the whole portal (sitemap + everything reachable), record pages, files and HTML tables."""
    import hashlib
    from .crawl import site
    with site.Portal() as portal:
        pages, files, tables = site.crawl(portal, refresh=a.refresh, max_nodes=a.max_nodes, max_depth=a.max_depth)
    config.DATA.mkdir(parents=True, exist_ok=True)
    pd_pages = __import__("pandas").DataFrame(pages)
    pd_pages["fetched_at"] = manifest.now()
    pd_pages.to_parquet(config.DATA / "site_pages.parquet", index=False)
    df = manifest.load()
    df, st = manifest.upsert(df, site.to_manifest_rows(files))
    manifest.save(df)
    out = config.INTERIM / "html_tables"
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for t in tables:
        sha = hashlib.sha256(t["df"].to_csv(index=False).encode()).hexdigest()
        t["df"].to_parquet(out / f"{sha}.parquet", index=False)
        index.append(dict(table_sha=sha, route=t["route"], idx=t["idx"], title=t["title"], section_path=t["section_path"],
                          n_rows=len(t["df"]), n_cols=t["df"].shape[1], columns=" | ".join(t["df"].columns)[:500]))
    __import__("pandas").DataFrame(index).drop_duplicates("table_sha").to_parquet(config.DATA / "site_tables.parquet", index=False)
    print(f"site-crawl: {len(pages)} nodes; manifest +{st['new']} new files; {len(index)} html tables extracted")


def cmd_site_report(a):
    import pandas as pd
    pages = pd.read_parquet(config.DATA / "site_pages.parquet")
    print(pages.groupby(["kind", "status"]).size().to_string())
    m = manifest.load()
    m = m[m.section.str.startswith("site/", na=False)]
    print(m.pivot_table(index="section", columns="ext", values="s3_url", aggfunc="count", fill_value=0).to_string())
    errs = pages[pages.status == "error"]
    if len(errs):
        print(errs[["route", "error"]].head(10).to_string())


def cmd_update(a):
    for f, ns in ((cmd_crawl, {"years": a.years, "refresh": False}), (cmd_download, {"years": a.years, "force": False, "retry_missing": False}),
                  (cmd_parse, {"all": False}), (cmd_validate, {}), (cmd_publish, {})):
        f(argparse.Namespace(**ns))


def main(argv=None):
    p = argparse.ArgumentParser(prog="dgca")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("crawl", help="discover files into the manifest"); s.add_argument("--years"); s.add_argument("--refresh", action="store_true"); s.set_defaults(f=cmd_crawl)
    s = sub.add_parser("download", help="fetch parseable files (skips unchanged)"); s.add_argument("--years"); s.add_argument("--force", action="store_true"); s.add_argument("--retry-missing", action="store_true"); s.set_defaults(f=cmd_download)
    s = sub.add_parser("parse", help="parse pending files; failures go to data/quarantine"); s.add_argument("--all", action="store_true"); s.set_defaults(f=cmd_parse)
    sub.add_parser("validate", help="check coverage and cross-checks; non-zero exit on problems").set_defaults(f=cmd_validate)
    sub.add_parser("publish", help="build parquet + semantic catalog into web/data").set_defaults(f=cmd_publish)
    sub.add_parser("status", help="coverage report").set_defaults(f=cmd_status)
    s = sub.add_parser("site-crawl", help="crawl the entire portal into site_pages/site_tables + manifest"); s.add_argument("--refresh", action="store_true"); s.add_argument("--max-nodes", type=int, default=4000); s.add_argument("--max-depth", type=int, default=5); s.set_defaults(f=cmd_site_crawl)
    sub.add_parser("site-report", help="summarise the site crawl").set_defaults(f=cmd_site_report)
    s = sub.add_parser("update", help="crawl -> download -> parse -> validate -> publish"); s.add_argument("--years"); s.set_defaults(f=cmd_update)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
