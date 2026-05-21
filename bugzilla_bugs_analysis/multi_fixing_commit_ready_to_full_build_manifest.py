#!/usr/bin/env python3
"""
================================================================================
FULL BUILD READINESS FILTER
================================================================================

PURPOSE:
--------
Reads the full build manifest output and filters bugs into two categories:
  1. buildable_bugs   — bugs that have at least one runnable test file
                        and can be built and tested
  2. skipped_bugs     — bugs where ALL test files are non-runnable
                        (android, header-only, manifest-only, etc.)

Each bug JSON preserves ALL original information from the manifest plus
adds runnable/skipped test file classification per commit pair.

INPUT:
------
outputs/multi_fixing_full_build_test_manifest/
└── <framework_group>/
    └── bug_<ID>.json

OUTPUT STRUCTURE:
-----------------
outputs/multi_fixing_commit_ready_to_full_build_manifest/
├── buildable_bugs/
│   └── <framework_group>/
│       └── bug_<ID>.json          ← full info + runnable/skipped file lists
├── skipped_bugs/
│   ├── android/
│   │   └── bug_<ID>.json          ← full info + skip reason
│   ├── non_runnable/
│   │   └── bug_<ID>.json
│   └── mixed_non_runnable/
│       └── bug_<ID>.json
└── overall_summary.json
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)

print(f"Working directory: {parent_dir}")


# ===========================================================================
# CONSTANTS
# ===========================================================================

INPUT_DIR  = "multi_fixing_full_build_test_manifest"
OUTPUT_DIR = "multi_fixing_commit_ready_to_full_build_manifest"

# Extensions that are manifest/support files — patch but don't run
NON_RUNNABLE_EXTENSIONS = {
    ".ini", ".toml", ".mjs", ".sjs", ".list",
    ".json", ".yaml", ".yml", ".pem", ".certspec",
    ".txt", ".in", ".build",
}

# Extensions that can never run on this Linux machine
UNSUPPORTED_EXTENSIONS = {
    ".kt",    # Android/Kotlin
    ".java",  # Android Java
    ".h",     # C++ headers — not runnable directly
    ".cpp",   # C++ source — only runnable if framework == gtest
    ".mp3", ".mp4", ".wav", ".adts", ".png",  # media/asset files
}

# Frameworks not runnable in this environment
UNSUPPORTED_FRAMEWORKS = {
    "android",    # needs Android device
    "marionette", # needs separate marionette setup
}

# Path patterns that indicate unsupported test types
UNSUPPORTED_PATH_PATTERNS = [
    "mobile/android/",
    "geckoview/",
]


# ===========================================================================
# RUNNABILITY CHECK
# ===========================================================================

def is_runnable(tf: Dict) -> Tuple[bool, str]:
    """
    Determine if a test file can be run on this Linux machine.
    GTest .cpp files are runnable via test name wildcard.
    Returns (runnable, reason_if_not_runnable).
    """
    filepath  = tf.get("filepath", "")
    framework = tf.get("framework", "unknown")
    ext       = Path(filepath).suffix.lower()

    # GTest .cpp files ARE runnable via ./mach gtest *TestName*
    if framework == "gtest" and ext == ".cpp":
        return True, ""

    if ext in NON_RUNNABLE_EXTENSIONS:
        return False, f"non-runnable extension ({ext})"
    if ext in UNSUPPORTED_EXTENSIONS:
        return False, f"unsupported on this platform ({ext})"
    if framework in UNSUPPORTED_FRAMEWORKS:
        return False, f"unsupported framework ({framework})"
    for pattern in UNSUPPORTED_PATH_PATTERNS:
        if pattern in filepath:
            return False, f"unsupported path ({pattern})"
    if not tf.get("mach_command", ""):
        return False, "no mach_command"

    return True, ""


def classify_bug_runnability(commit_pairs: List[Dict]) -> Tuple[bool, str, str]:
    """
    Check if a bug has at least one runnable test file across all commit pairs.

    Returns:
      (has_runnable, skip_category, skip_reason)
      - has_runnable   : True if at least one test file is runnable
      - skip_category  : "android" | "non_runnable" | "mixed_non_runnable" | ""
      - skip_reason    : human readable reason if not runnable
    """
    all_test_files = [
        tf
        for pair in commit_pairs
        for tf in pair.get("test_files", [])
    ]

    if not all_test_files:
        return False, "non_runnable", "no test files found"

    has_android = any(
        tf.get("framework") == "android"
        or ".kt" in tf.get("filepath", "")
        or ".java" in tf.get("filepath", "")
        or "mobile/android/" in tf.get("filepath", "")
        or "geckoview/" in tf.get("filepath", "")
        for tf in all_test_files
    )

    runnable_files  = []
    skipped_files   = []

    for tf in all_test_files:
        runnable, reason = is_runnable(tf)
        if runnable:
            runnable_files.append(tf)
        else:
            skipped_files.append((tf, reason))

    if runnable_files:
        return True, "", ""

    # All files are non-runnable — determine category
    if has_android:
        return False, "android", "all test files require Android device"

    # Check if it's purely manifest/support files
    all_non_runnable_exts = all(
        Path(tf.get("filepath", "")).suffix.lower() in NON_RUNNABLE_EXTENSIONS
        for tf in all_test_files
    )
    if all_non_runnable_exts:
        reasons = sorted(set(r for _, r in skipped_files))
        return False, "non_runnable", ", ".join(reasons)

    # Mixed non-runnable (headers, .cpp without gtest, etc.)
    reasons = sorted(set(r for _, r in skipped_files))
    return False, "mixed_non_runnable", ", ".join(reasons)


def classify_test_files_per_pair(commit_pairs: List[Dict]) -> List[Dict]:
    """
    For each commit pair, split test_files into runnable and skipped lists.
    Preserves ALL original commit pair information and adds the classification.
    """
    enriched_pairs = []
    for pair in commit_pairs:
        test_files     = pair.get("test_files", [])
        runnable_files = []
        skipped_files  = []

        for tf in test_files:
            runnable, reason = is_runnable(tf)
            if runnable:
                runnable_files.append(tf)
            else:
                skipped_files.append({
                    **tf,
                    "skip_reason": reason,
                })

        enriched_pairs.append({
            # Preserve ALL original commit pair fields
            "fixing_commit":   pair.get("fixing_commit"),
            "parent_commit":   pair.get("parent_commit"),
            "repo_found_in":   pair.get("repo_found_in"),
            "parent_metadata": pair.get("parent_metadata", {}),
            "frameworks":      pair.get("frameworks", []),
            # Original test_files preserved
            "test_files":      test_files,
            # Added classification
            "runnable_test_files": runnable_files,
            "skipped_test_files":  skipped_files,
            "total_runnable":      len(runnable_files),
            "total_skipped":       len(skipped_files),
        })

    return enriched_pairs


def build_output_payload(bug: Dict, enriched_pairs: List[Dict], skip_reason: str = "") -> Dict:
    """
    Build the output JSON payload for a bug.
    Preserves ALL original fields and adds runnability classification.
    """
    total_runnable = sum(p["total_runnable"] for p in enriched_pairs)
    total_skipped  = sum(p["total_skipped"]  for p in enriched_pairs)

    return {
        # ── All original fields preserved ──────────────────────────
        "bug_id":             bug["bug_id"],
        "framework_group":    bug.get("framework_group", ""),
        "all_frameworks":     bug.get("all_frameworks", []),
        "total_commit_pairs": bug.get("total_commit_pairs", 0),
        "total_test_files":   bug.get("total_test_files", 0),
        "source":             bug.get("source", ""),
        "generated_at":       bug.get("generated_at", ""),
        # ── Enriched commit pairs with runnability classification ───
        "commit_pairs":       enriched_pairs,
        # ── Added runnability summary ───────────────────────────────
        "total_runnable_test_files": total_runnable,
        "total_skipped_test_files":  total_skipped,
        "skip_reason":               skip_reason or None,
        "filtered_at":               datetime.now().isoformat(),
    }


# ===========================================================================
# INPUT LOADER
# ===========================================================================

def load_all_bugs(input_base: Path) -> List[Dict]:
    """
    Walk all framework group folders and load every bug_<ID>.json.
    Skips group_summary.json and overall_summary.json files.
    """
    bugs = []
    for group_dir in sorted(input_base.iterdir()):
        if not group_dir.is_dir():
            continue
        for bug_file in sorted(group_dir.glob("bug_*.json")):
            try:
                data = json.loads(bug_file.read_text(encoding="utf-8"))
                bugs.append(data)
            except Exception as e:
                print(f"  WARNING: could not read {bug_file}: {e}")
    print(f"Loaded {len(bugs)} bugs from {input_base}\n")
    return bugs


# ===========================================================================
# OUTPUT WRITER
# ===========================================================================

class OutputWriter:

    BUILDABLE_DIR = "buildable_bugs"
    SKIPPED_DIR   = "skipped_bugs"

    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.base.mkdir(parents=True, exist_ok=True)
        # Create skipped subcategory dirs
        for subdir in ["android", "non_runnable", "mixed_non_runnable"]:
            (self.base / self.SKIPPED_DIR / subdir).mkdir(parents=True, exist_ok=True)

    def save_buildable(self, framework_group: str, bug_id: str, payload: Dict):
        group_dir = self.base / self.BUILDABLE_DIR / framework_group
        group_dir.mkdir(parents=True, exist_ok=True)
        out_path = group_dir / f"bug_{bug_id}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def save_skipped(self, skip_category: str, bug_id: str, payload: Dict):
        out_dir  = self.base / self.SKIPPED_DIR / skip_category
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"bug_{bug_id}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def save_overall_summary(
        self,
        buildable:        Dict[str, List[str]],    # framework_group → [bug_ids]
        skipped:          Dict[str, List[Dict]],   # category → [bug payloads]
        total_bugs:       int,
        buildable_bugs:   List[Dict],              # full payloads for commit counting
    ):
        total_buildable = sum(len(v) for v in buildable.values())
        total_skipped   = sum(len(v) for v in skipped.values())

        # Count total commits to build (fixing + parent per pair)
        total_commit_pairs  = sum(b.get("total_commit_pairs", 0) for b in buildable_bugs)
        total_commits_to_build = total_commit_pairs * 2  # fixing + parent per pair

        buildable_rows = []
        for group, bug_ids in sorted(buildable.items()):
            # Get payloads for this group to count commits
            group_bugs   = [b for b in buildable_bugs if b.get("framework_group") == group]
            group_pairs  = sum(b.get("total_commit_pairs", 0) for b in group_bugs)
            group_commits = group_pairs * 2
            buildable_rows.append({
                "framework_group":      group,
                "total_bugs":           len(bug_ids),
                "total_commit_pairs":   group_pairs,
                "total_commits_to_build": group_commits,  # fixing + parent
                "bug_ids":              bug_ids,
            })

        skipped_rows = []
        for cat, bugs in sorted(skipped.items()):
            skipped_rows.append({
                "skip_category": cat,
                "total_bugs":    len(bugs),
                "bug_ids":       [b["bug_id"] for b in bugs],
            })

        summary = {
            "generated_at":              datetime.now().isoformat(),
            "total_bugs_input":          total_bugs,
            "total_buildable_bugs":      total_buildable,
            "total_skipped_bugs":        total_skipped,
            "total_commit_pairs":        total_commit_pairs,
            "total_commits_to_build":    total_commits_to_build,  # fixing + parent
            "buildable_by_framework":    buildable_rows,
            "skipped_by_category":       skipped_rows,
        }
        out_path = self.base / "overall_summary.json"
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n✓ overall_summary.json → {out_path}")
        return summary


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

class ReadyToFullBuildFilterPipeline:

    def __init__(self):
        self.script_dir   = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"

        self.input_base  = self.outputs_base / INPUT_DIR
        self.output_base = self.outputs_base / OUTPUT_DIR
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.writer = OutputWriter(self.output_base)

        print(f"Input:  {self.input_base}")
        print(f"Output: {self.output_base}\n")

    def run(self):
        print("=" * 80)
        print("FULL BUILD READINESS FILTER")
        print("=" * 80 + "\n")

        bugs = load_all_bugs(self.input_base)
        if not bugs:
            print("No bugs to process.")
            return

        total = len(bugs)

        # Track results
        buildable_groups: Dict[str, List[str]]    = defaultdict(list)
        skipped_cats:     Dict[str, List[Dict]]   = defaultdict(list)

        # Track buildable payloads for commit counting
        buildable_payloads: List[Dict] = []

        for idx, bug in enumerate(bugs, 1):
            bug_id         = bug["bug_id"]
            framework_grp  = bug.get("framework_group", "unknown")
            commit_pairs   = bug.get("commit_pairs", [])

            print(f"[{idx}/{total}] Bug {bug_id}  group={framework_grp} …", end=" ", flush=True)

            # Check runnability
            has_runnable, skip_cat, skip_reason = classify_bug_runnability(commit_pairs)

            # Enrich commit pairs with per-file runnability classification
            enriched_pairs = classify_test_files_per_pair(commit_pairs)

            # Build output payload preserving all original info
            payload = build_output_payload(
                bug,
                enriched_pairs,
                skip_reason if not has_runnable else "",
            )

            if has_runnable:
                self.writer.save_buildable(framework_grp, bug_id, payload)
                buildable_groups[framework_grp].append(bug_id)
                buildable_payloads.append(payload)
                runnable_count = payload["total_runnable_test_files"]
                skipped_count  = payload["total_skipped_test_files"]
                print(f"✓ buildable  runnable={runnable_count}  skipped={skipped_count}")
            else:
                self.writer.save_skipped(skip_cat, bug_id, payload)
                skipped_cats[skip_cat].append(payload)
                print(f"✗ skipped  category={skip_cat}  reason={skip_reason[:60]}")

        # Save overall summary
        overall = self.writer.save_overall_summary(
            dict(buildable_groups),
            dict(skipped_cats),
            total,
            buildable_payloads,
        )

        self._print_summary(overall)

    def _print_summary(self, overall: Dict):
        print("\n" + "=" * 80)
        print("FULL BUILD READINESS FILTER — SUMMARY")
        print("=" * 80)
        print(f"  Total bugs input                   : {overall['total_bugs_input']}")
        print(f"  Buildable bugs                     : {overall['total_buildable_bugs']}")
        print(f"  Skipped bugs                       : {overall['total_skipped_bugs']}")
        print(f"  Total commit pairs                 : {overall['total_commit_pairs']}")
        print(f"  Total commits to build             : {overall['total_commits_to_build']}  (fixing + parent per pair)")

        print(f"\n  ── Buildable by framework group ────────────────────────────")
        for row in sorted(overall["buildable_by_framework"], key=lambda x: -x["total_bugs"]):
            print(
                f"    {row['framework_group']:40s}"
                f"  bugs={row['total_bugs']:4d}"
                f"  pairs={row['total_commit_pairs']:4d}"
                f"  commits={row['total_commits_to_build']:4d}"
            )

        print(f"\n  ── Skipped by category ─────────────────────────────────────")
        for row in sorted(overall["skipped_by_category"], key=lambda x: -x["total_bugs"]):
            print(f"    {row['skip_category']:40s}  bugs={row['total_bugs']:4d}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    pipeline = ReadyToFullBuildFilterPipeline()
    pipeline.run()

    print("\n" + "=" * 80)
    print("✓  STEP COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
