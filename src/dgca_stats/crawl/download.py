"""Download files listed in the manifest. Idempotent: unchanged files are skipped, identical content is stored once."""
import fnmatch
import hashlib
import shutil

import pandas as pd

from .. import config
from ..http import _throttle


def select(df: pd.DataFrame, tables=None, exts=("xlsx",), fys=None) -> pd.Series:
    m = df["ext"].isin(exts)
    if tables:
        m &= df["table_id"].isin(tables)
    if fys:
        m &= df["fy"].isin(fys)
    return m


def download(sess, df: pd.DataFrame, mask: pd.Series, force=False, log=print) -> dict:
    stats = {"downloaded": 0, "not_modified": 0, "missing": 0, "error": 0, "dedup": 0}
    sha_first = {s: u for u, s in zip(df["s3_url"], df["content_sha256"]) if isinstance(s, str)}
    for i in df.index[mask]:
        url = df.at[i, "s3_url"]
        headers = {}
        have = isinstance(df.at[i, "content_sha256"], str) and df.at[i, "raw_path"] and (config.ROOT / str(df.at[i, "raw_path"])).exists()
        if have and not force and isinstance(df.at[i, "etag"], str):
            headers["If-None-Match"] = df.at[i, "etag"]
        try:
            _throttle()
            r = sess.get(url, headers=headers, timeout=120)
        except Exception as e:  # network failure: keep state, retry next run
            df.at[i, "status"], df.at[i, "error"] = "error", str(e)[:200]
            stats["error"] += 1
            continue
        df.at[i, "http_status"] = r.status_code
        if r.status_code == 304:
            stats["not_modified"] += 1
            continue
        if r.status_code in (403, 404):  # S3 answers 403 for absent keys
            df.at[i, "status"], df.at[i, "error"] = "missing", f"HTTP {r.status_code}"
            df.at[i, "parse_status"] = "skipped"
            stats["missing"] += 1
            continue
        if r.status_code != 200:
            df.at[i, "status"], df.at[i, "error"] = "error", f"HTTP {r.status_code}"
            stats["error"] += 1
            continue
        sha = hashlib.sha256(r.content).hexdigest()
        ext = df.at[i, "ext"]
        dest = config.RAW / sha[:2] / f"{sha}.{ext}"
        if sha in sha_first and sha_first[sha] != url:
            df.at[i, "duplicate_of"] = sha_first[sha]
            stats["dedup"] += 1
        else:
            sha_first.setdefault(sha, url)
            df.at[i, "duplicate_of"] = None
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
        prev = df.at[i, "content_sha256"]
        changed = pd.isna(prev) or prev != sha
        df.at[i, "content_sha256"] = sha
        df.at[i, "raw_path"] = str(dest.relative_to(config.ROOT)) if dest.is_relative_to(config.ROOT) else str(dest)
        df.at[i, "etag"] = r.headers.get("ETag")
        df.at[i, "last_modified"] = r.headers.get("Last-Modified")
        df.at[i, "size"] = len(r.content)
        df.at[i, "status"], df.at[i, "error"] = "downloaded", None
        if changed:
            df.at[i, "parse_status"] = "pending"
        stats["downloaded"] += 1
    log(f"download: {stats}")
    return stats
