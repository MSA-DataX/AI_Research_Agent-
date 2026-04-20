from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from typing import Optional

from config import (
    AUTO_CLEANUP_ENABLED,
    AUTO_CLEANUP_INTERVAL_HOURS,
    AUTO_CLEANUP_LOGS_DAYS,
    AUTO_CLEANUP_MARKER,
    AUTO_CLEANUP_RBS_DAYS,
    AUTO_CLEANUP_RESULTS_DAYS,
    LOG_DIR,
    RESULTS_DIR,
)


@dataclass
class PruneResult:
    deleted_files: list[str]
    freed_bytes: int

    @property
    def count(self) -> int:
        return len(self.deleted_files)


def _dir_stats(path: str) -> dict:
    if not os.path.isdir(path):
        return {"path": path, "files": 0, "bytes": 0}
    total = 0
    count = 0
    for name in os.listdir(path):
        fp = os.path.join(path, name)
        if os.path.isfile(fp):
            try:
                total += os.path.getsize(fp)
                count += 1
            except OSError:
                pass
    return {"path": path, "files": count, "bytes": total}


def stats() -> dict:
    from config import DB_PATH

    logs = _dir_stats(LOG_DIR)
    results = _dir_stats(RESULTS_DIR)
    db_bytes = os.path.getsize(DB_PATH) if os.path.isfile(DB_PATH) else 0

    try:
        import research_box as rb_store
        rb_count = len(rb_store.list_all(limit=10000))
    except Exception:
        rb_count = None

    return {
        "logs": logs,
        "results": results,
        "database": {"path": DB_PATH, "bytes": db_bytes, "rb_count": rb_count},
        "total_bytes": logs["bytes"] + results["bytes"] + db_bytes,
    }


def _prune_dir(path: str, days_old: int, dry_run: bool) -> PruneResult:
    if not os.path.isdir(path):
        return PruneResult([], 0)
    cutoff = time.time() - days_old * 86400
    deleted: list[str] = []
    freed = 0
    for name in os.listdir(path):
        fp = os.path.join(path, name)
        if not os.path.isfile(fp):
            continue
        try:
            mtime = os.path.getmtime(fp)
            size = os.path.getsize(fp)
        except OSError:
            continue
        if mtime < cutoff:
            if not dry_run:
                try:
                    os.remove(fp)
                except OSError:
                    continue
            deleted.append(name)
            freed += size
    return PruneResult(deleted, freed)


def prune_logs(days_old: int = 30, dry_run: bool = True) -> PruneResult:
    return _prune_dir(LOG_DIR, days_old, dry_run)


def prune_results(days_old: int = 30, dry_run: bool = True) -> PruneResult:
    return _prune_dir(RESULTS_DIR, days_old, dry_run)


def prune_rbs(
    days_old: int = 90,
    status_filter: Optional[list[str]] = None,
    dry_run: bool = True,
) -> PruneResult:
    import research_box as rb_store

    cutoff = time.time() - days_old * 86400
    deleted: list[str] = []
    for rb in rb_store.list_all(limit=10000):
        try:
            ts_struct = time.strptime(rb.updated_at[:19], "%Y-%m-%dT%H:%M:%S")
            ts = time.mktime(ts_struct)
        except Exception:
            continue
        if ts >= cutoff:
            continue
        if status_filter and rb.status not in status_filter:
            continue
        if not dry_run:
            rb_store.delete(rb.id)
        deleted.append(rb.id)
    return PruneResult(deleted, 0)


def auto_cleanup(force: bool = False) -> dict | None:
    """Startup-Hook: läuft max. 1× pro AUTO_CLEANUP_INTERVAL_HOURS, still und schnell.

    Returns:
        dict mit Zusammenfassung wenn gelaufen, None wenn geskippt (throttled/disabled/error).
    """
    if not AUTO_CLEANUP_ENABLED and not force:
        return None

    now = time.time()
    if not force and os.path.exists(AUTO_CLEANUP_MARKER):
        try:
            last = os.path.getmtime(AUTO_CLEANUP_MARKER)
            if now - last < AUTO_CLEANUP_INTERVAL_HOURS * 3600:
                return None
        except OSError:
            pass

    try:
        r_logs = prune_logs(AUTO_CLEANUP_LOGS_DAYS, dry_run=False)
        r_results = prune_results(AUTO_CLEANUP_RESULTS_DAYS, dry_run=False)
        r_rbs = prune_rbs(
            AUTO_CLEANUP_RBS_DAYS,
            status_filter=["error", "cancelled", "max_iterations"],
            dry_run=False,
        )
    except Exception as e:
        return {"error": str(e)[:200]}

    try:
        with open(AUTO_CLEANUP_MARKER, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S"))
    except OSError:
        pass

    return {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "logs_removed": r_logs.count,
        "results_removed": r_results.count,
        "rbs_removed": r_rbs.count,
        "bytes_freed": r_logs.freed_bytes + r_results.freed_bytes,
    }


def _fmt(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def main() -> int:
    p = argparse.ArgumentParser(description="Cleanup old logs, results and research boxes.")
    p.add_argument("--stats", action="store_true", help="show sizes only (default)")
    p.add_argument("--logs-days", type=int, default=30, help="delete logs older than N days")
    p.add_argument("--results-days", type=int, default=30, help="delete results older than N days")
    p.add_argument("--rbs-days", type=int, default=0, help="delete RBs older than N days (0 = skip)")
    p.add_argument("--rbs-status", type=str, default="error,cancelled,max_iterations",
                   help="comma-separated statuses to prune (only active if --rbs-days>0)")
    p.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = p.parse_args()

    s = stats()
    print(f"logs    : {s['logs']['files']:>4} files · {_fmt(s['logs']['bytes'])}")
    print(f"results : {s['results']['files']:>4} files · {_fmt(s['results']['bytes'])}")
    rb_line = f"  ({s['database']['rb_count']} RBs)" if s['database']['rb_count'] is not None else ""
    print(f"db      : {_fmt(s['database']['bytes'])}{rb_line}")
    print(f"total   : {_fmt(s['total_bytes'])}")

    if args.stats:
        return 0

    dry = not args.apply
    mode = "[DRY RUN]" if dry else "[APPLY]"
    print(f"\n{mode} Pruning ...")

    r_logs = prune_logs(args.logs_days, dry_run=dry)
    print(f"logs    : {r_logs.count} files, {_fmt(r_logs.freed_bytes)} ({'would be ' if dry else ''}removed, >{args.logs_days}d old)")

    r_res = prune_results(args.results_days, dry_run=dry)
    print(f"results : {r_res.count} files, {_fmt(r_res.freed_bytes)} ({'would be ' if dry else ''}removed, >{args.results_days}d old)")

    if args.rbs_days > 0:
        statuses = [s.strip() for s in args.rbs_status.split(",") if s.strip()]
        r_rbs = prune_rbs(args.rbs_days, status_filter=statuses or None, dry_run=dry)
        print(f"rbs     : {r_rbs.count} boxes ({'would be ' if dry else ''}removed, status in {statuses})")

    if dry:
        print("\n(Nothing deleted. Use --apply to actually remove.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
