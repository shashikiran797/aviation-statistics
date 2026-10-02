"""Publish the whole-site catalog: every file and extracted HTML table the crawler found, searchable in the browser."""
import shutil

import pandas as pd

from .. import config

MAX_TABLE_ROWS = 20000


def build(man: pd.DataFrame, out) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    m = man.copy()
    stats = m.section == config.SECTION
    files = pd.DataFrame({
        "section": m.section.str.replace("^site/", "", regex=True).where(~stats, "air_transport_statistics"),
        "path": m.entity.where(~stats, "Data & Reports > Aviation data statistics > Air transport"),
        "title": m.title, "fy": m.fy, "ext": m.ext, "url": m.s3_url,
        "kind": m.s3_url.str.startswith("dgca-dynamic://").map({True: "dynamic", False: "file"}),
        "in_dashboards": (m.parse_status == "ok") & stats,
        "available": m.status.ne("missing"),
    })
    # statistics pdf twins of parsed xlsx are noise in a browse list: keep one row per (title) for stats
    files = pd.concat([files[~stats.values], files[stats.values].drop_duplicates(["title", "ext", "path", "fy"])], ignore_index=True)
    files = files.drop_duplicates("url").sort_values(["section", "path", "title"]).reset_index(drop=True)
    files.to_parquet(out / "site_files.parquet", index=False, compression="zstd")

    n_tables = 0
    idx_path = config.DATA / "site_tables.parquet"
    if idx_path.exists():
        idx = pd.read_parquet(idx_path)
        tdir = out / "tables"
        tdir.mkdir(exist_ok=True)
        keep = []
        for _, r in idx.iterrows():
            src = config.INTERIM / "html_tables" / f"{r.table_sha}.parquet"
            if src.exists() and r.n_rows <= MAX_TABLE_ROWS:
                shutil.copy(src, tdir / f"{r.table_sha}.parquet")
                keep.append(r)
        idx = pd.DataFrame(keep)
        if len(idx):
            idx["section"] = idx.section_path.str.split(" > ").str[0]
        idx.to_parquet(out / "site_tables.parquet", index=False, compression="zstd")
        n_tables = len(idx)
    return {"files": len(files), "tables": n_tables}
