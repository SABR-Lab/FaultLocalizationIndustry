#!/usr/bin/env python3
"""
Classify all bugs across both workers into buckets:
  - needs_python2/     : all commits need Python 2
  - needs_python3/     : OOM, cbindgen, Rust pin, configure (+ mixed bugs)
  - partial_builds/    : some commits built, some failed
  - validated/         : fully built, tests pass on fixing, fail on parent
  - not_validated/     : fully built but wrong test outcomes

Sources read:
  - worker_N/needs_python2/      (retry_context.json)
  - worker_N/failed_builds/      (build_failure.json  — reliable reason field)
  - worker_N/successful_builds/  (test_results.json)

Output:
  classification/
  ├── needs_python2/
  ├── needs_python3/
  ├── partial_builds/
  ├── validated/
  ├── not_validated/
  └── index.json
"""

import json
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE    = Path("/data/FaultLocalizationIndustry/bugzilla_bugs_analysis/outputs/multi_fixing_commit_full_build")
WORKERS = [1, 2]
OUT     = BASE / "classification"

BUCKETS = ["needs_python2", "needs_python3", "partial_builds",
           "successful_builds/validated", "successful_builds/not_validated"]
for b in BUCKETS:
    (OUT / b).mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Signal definitions
# ---------------------------------------------------------------------------
PYTHON2_SIGNALS = [
    # collections API removed in Python 3.10
    "ImportError: cannot import name 'Iterable'",
    "ImportError: cannot import name 'OrderedDict'",
    "ImportError: cannot import name 'get_virtualenv_base_dir'",
    "AttributeError: module 'collections' has no attribute 'Sequence'",
    "AttributeError: module 'collections' has no attribute 'Callable'",
    # Python 2 builtins
    "No module named '__builtin__'",
    "import __builtin__",
    "import distutils",
    "import imp",
    # platform.dist removed in Python 3.8
    "AttributeError: module 'platform' has no attribute 'dist'",
    # mach virtualenv / bootstrap issues
    "mach bootstrap",
    "create-mach-environment",
    "MACH_USE_SYSTEM_PYTHON",
    "virtualenvs/mach/bin/python",
    "ModuleNotFoundError: No module named 'mach",
    "from collections import",
    # explicit Python version rejection
    "Python 2.7 or above (but not Python 3) is required",
    # Python 2 syntax errors
    "IndentationError",
    "D = TypeVar",
]

PYTHON3_SIGNALS = {
    "oom": [
        "Parallelism determined by memory",  # last line before OOM kill
        "Killed",
        "gmake.*Killed",
        "Cannot allocate memory",
        "build timed out after",             # timeout = likely OOM
        "BUILD_TIMEOUT' is not defined",     # script crash caused by OOM timeout
        "rc=1",                              # OOM kill leaves mach with exit code 1
    ],
    "cbindgen": [
        "Keyframe",
        "duplicate key",
        "cbindgen",
    ],
    "rust_pin": [
        "Rust compiler",
        "is too old",
        "rustc version",
    ],
    "configure": [
        "Fix above errors and then restart",
        "configure.py",
        "Total wall time",
        "Be sure to run |mach build| to pick up any changes",
    ],
    "pipeline_error": [
        "ResolutionImpossible",
        "Could not run mach",
    ],
    "genuine_build": [
        "raise Exception",
        "compiler warnings present",
    ],
}

MANIFEST_BASE = Path("/data/FaultLocalizationIndustry/bugzilla_bugs_analysis/outputs/multi_fixing_commit_ready_to_full_build_manifest/buildable_bugs")


def load_from_manifest(bug_id: str) -> dict:
    """Load full bug JSON from input manifest by bug ID."""
    for group_dir in sorted(MANIFEST_BASE.iterdir()):
        if not group_dir.is_dir():
            continue
        f = group_dir / f"bug_{bug_id}.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}
def classify_reason(reason: str) -> str:
    if not reason:
        return "unknown"
    for sig in PYTHON2_SIGNALS:
        if sig in reason:
            return "python2"
    for category, signals in PYTHON3_SIGNALS.items():
        for sig in signals:
            if re.search(sig, reason):
                return category
    return "unknown"


def write_bug(bucket: str, bug_id: str, payload: dict):
    out_dir = OUT / bucket / f"bug_{bug_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "classification.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_failed_builds() -> dict:
    """bug_id -> list of classified commit entries"""
    bugs = defaultdict(list)
    for worker_id in WORKERS:
        fb_dir = BASE / f"worker_{worker_id}" / "failed_builds"
        if not fb_dir.exists():
            continue
        for bug_dir in sorted(fb_dir.iterdir()):
            if not bug_dir.is_dir() or not bug_dir.name.startswith("bug_"):
                continue
            f = bug_dir / "build_failure.json"
            if not f.exists():
                continue
            try:
                entries = json.loads(f.read_text())
                if not isinstance(entries, list):
                    entries = [entries]
            except Exception as e:
                print(f"  WARNING: {f}: {e}")
                continue

            seen = set()
            for entry in entries:
                h = entry.get("commit_hash", "")[:12]
                if h in seen:
                    continue
                seen.add(h)
                reason   = entry.get("reason", "")
                category = classify_reason(reason)
                bugs[entry.get("bug_id", "unknown")].append({
                    "commit_hash": h,
                    "role":        entry.get("role", "unknown"),
                    "failed_step": entry.get("failed_step", ""),
                    "reason":      reason[:120],
                    "category":    category,
                    "worker_id":   worker_id,
                    "source":      "failed_builds",
                })
    return bugs


def load_needs_python2() -> dict:
    """bug_id -> retry context data"""
    bugs = {}
    for worker_id in WORKERS:
        py2_dir = BASE / f"worker_{worker_id}" / "needs_python2"
        if not py2_dir.exists():
            continue
        for bug_dir in sorted(py2_dir.iterdir()):
            if not bug_dir.is_dir() or not bug_dir.name.startswith("bug_"):
                continue
            f = bug_dir / "retry_context.json"
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text())
            except Exception as e:
                print(f"  WARNING: {f}: {e}")
                continue
            bug_id = data.get("bug_id", "unknown")
            if bug_id in bugs:
                continue  # deduplicate — take first worker
            bugs[bug_id] = {
                "bug_id":          bug_id,
                "framework_group": data.get("framework_group", "unknown"),
                "original_bug":    data.get("original_bug", {}),
                "worker_id":       worker_id,
                "failing_commits": data.get("failing_commits", []),
            }
    return bugs


def load_successful_builds() -> dict:
    """bug_id -> test_results data"""
    bugs = {}
    for worker_id in WORKERS:
        sb_dir = BASE / f"worker_{worker_id}" / "successful_builds"
        if not sb_dir.exists():
            continue
        for bug_dir in sorted(sb_dir.iterdir()):
            if not bug_dir.is_dir() or not bug_dir.name.startswith("bug_"):
                continue
            f = bug_dir / "test_results.json"
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text())
            except Exception as e:
                print(f"  WARNING: {f}: {e}")
                continue
            bug_id = data.get("bug_id", "unknown")
            if bug_id in bugs:
                continue  # deduplicate — take first worker
            bugs[bug_id] = data
    return bugs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading data from both workers...")
    failed_builds     = load_failed_builds()
    needs_py2         = load_needs_python2()
    successful_builds = load_successful_builds()

    # All unique bug IDs across all sources
    all_bug_ids = set(failed_builds.keys()) | set(needs_py2.keys()) | set(successful_builds.keys())
    print(f"Total unique bugs found: {len(all_bug_ids)}\n")

    stats   = defaultdict(int)
    index   = {
        "generated_at":  datetime.now().isoformat(),
        "needs_python2": [],
        "needs_python3": [],
        "partial_builds": [],
        "successful_builds": {
            "validated":     [],
            "not_validated": [],
        },
    }

    for bug_id in sorted(all_bug_ids):
        sb_data  = successful_builds.get(bug_id)
        fb_commits = failed_builds.get(bug_id, [])
        py2_data   = needs_py2.get(bug_id, {})

        framework = (
            (sb_data or {}).get("framework_group")
            or py2_data.get("framework_group")
            or (fb_commits[0].get("framework_group") if fb_commits else None)
            or "unknown"
        )

        # ── CASE 1: Bug has successful builds (from successful_builds/) ──
        if sb_data:
            overall  = sb_data.get("overall_status", "unknown")
            commits  = sb_data.get("commits", [])
            n_built  = sum(1 for c in commits if c.get("build_ok"))
            n_failed = sum(1 for c in commits if not c.get("build_ok"))

            payload = {
                "bug_id":          bug_id,
                "framework_group": framework,
                "overall_status":  overall,
                "classified_at":   datetime.now().isoformat(),
                "source":          "successful_builds",
                "commits_built":   n_built,
                "commits_failed":  n_failed,
                "commits": [
                    {
                        "commit_hash": c.get("commit_hash", "")[:12],
                        "role":        c.get("role"),
                        "expected":    c.get("expected"),
                        "build_ok":    c.get("build_ok"),
                        "tests_passed": sum(1 for t in c.get("tests", []) if t["result"] == "pass"),
                        "tests_failed": sum(1 for t in c.get("tests", []) if t["result"] not in ("pass", "skipped")),
                        "tests_skipped": sum(1 for t in c.get("tests", []) if t["result"] == "skipped"),
                    }
                    for c in commits
                ],
            }

            if n_built > 0 and n_failed > 0:
                bucket = "partial_builds"
            elif overall == "pass":
                bucket = "successful_builds/validated"
            else:
                bucket = "successful_builds/not_validated"

            stats[bucket.replace("/", "_")] += 1
            if bucket == "partial_builds":
                index["partial_builds"].append({"bug_id": bug_id, "framework_group": framework, "overall_status": overall})
            else:
                sub = bucket.split("/")[-1]
                index["successful_builds"][sub].append({"bug_id": bug_id, "framework_group": framework, "overall_status": overall})
            write_bug(bucket, bug_id, payload)
            print(f"  [{bucket:15s}] Bug {bug_id:10s}  status={overall}  built={n_built}  failed={n_failed}")
            continue

        # ── CASE 2: Bug only in failed_builds / needs_python2 ──
        # Merge commit classifications from both sources
        commit_classifications = []

        # From failed_builds (reliable)
        for c in fb_commits:
            commit_classifications.append(c)

        # From needs_python2 failing_commits — reclassify using signals
        for c in py2_data.get("failing_commits", []):
            h = c.get("commit_hash", "")[:12]
            if any(x["commit_hash"] == h for x in commit_classifications):
                continue
            build_error = c.get("build_error", "")
            category    = classify_reason(build_error)
            commit_classifications.append({
                "commit_hash": h,
                "role":        c.get("role", "unknown"),
                "reason":      build_error[:120],
                "category":    category,
                "source":      "needs_python2_retry_context",
            })

        if not commit_classifications:
            continue

        py2_commits = [c for c in commit_classifications if c["category"] == "python2"]
        py3_commits = [c for c in commit_classifications if c["category"] != "python2"]
        py3_cats    = list(set(c["category"] for c in py3_commits))

        payload = {
            "bug_id":          bug_id,
            "framework_group": framework,
            "original_bug":    py2_data.get("original_bug") or load_from_manifest(bug_id),
            "classified_at":   datetime.now().isoformat(),
            "commits":         commit_classifications,
            "summary": {
                "total_commits":      len(commit_classifications),
                "python2_count":      len(py2_commits),
                "python3_count":      len(py3_commits),
                "python3_categories": py3_cats,
            },
        }

        if py2_commits and not py3_commits:
            bucket = "needs_python2"
            payload["retry_with"] = "python2"
            stats["needs_python2"] += 1
            index["needs_python2"].append({
                "bug_id": bug_id, "framework_group": framework,
                "commits": len(py2_commits),
            })

        elif py3_commits and not py2_commits:
            dominant = py3_cats[0] if len(py3_cats) == 1 else "mixed"
            bucket   = "needs_python3"
            payload["retry_with"] = f"python3_{dominant}"
            stats[f"needs_python3_{dominant}"] += 1
            index["needs_python3"].append({
                "bug_id": bug_id, "framework_group": framework,
                "python3_category": dominant,
            })

        else:
            # Mixed — fixing needs Python 2, parent needs Python 3
            bucket = "needs_python3"
            payload["retry_with"] = "mixed_python2_fixing_python3_parent"
            payload["note"] = "fixing commit needs Python 2, parent needs Python 3 fix"
            stats["mixed"] += 1
            index["needs_python3"].append({
                "bug_id": bug_id, "framework_group": framework,
                "python3_category": "mixed",
                "note": "fixing=python2 parent=python3",
            })

        write_bug(bucket, bug_id, payload)
        print(f"  [{bucket:15s}] Bug {bug_id:10s}  "
              f"py2={len(py2_commits)}  py3={len(py3_commits)}  "
              f"py3_cats={py3_cats}  frameworks={framework}")

    # Write index
    index["stats"] = dict(stats)
    (OUT / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    # Summary
    print(f"\n{'='*60}")
    print(f"CLASSIFICATION COMPLETE")
    print(f"{'='*60}")
    print(f"  needs_python2              : {stats['needs_python2']}")
    print(f"  needs_python3 (oom)        : {stats.get('needs_python3_oom', 0)}")
    print(f"  needs_python3 (cbindgen)   : {stats.get('needs_python3_cbindgen', 0)}")
    print(f"  needs_python3 (rust_pin)   : {stats.get('needs_python3_rust_pin', 0)}")
    print(f"  needs_python3 (configure)  : {stats.get('needs_python3_configure', 0)}")
    print(f"  needs_python3 (pipeline)   : {stats.get('needs_python3_pipeline_error', 0)}")
    print(f"  needs_python3 (genuine)    : {stats.get('needs_python3_genuine_build', 0)}")
    print(f"  needs_python3 (unknown)    : {stats.get('needs_python3_unknown', 0)}")
    print(f"  needs_python3 (mixed)      : {stats['mixed']}")
    print(f"  partial_builds             : {stats['partial_builds']}")
    print(f"  successful_builds/validated    : {stats.get('successful_builds_validated', 0)}")
    print(f"  successful_builds/not_validated: {stats.get('successful_builds_not_validated', 0)}")
    print(f"  Output : {OUT}")
    print(f"  Index  : {OUT / 'index.json'}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
