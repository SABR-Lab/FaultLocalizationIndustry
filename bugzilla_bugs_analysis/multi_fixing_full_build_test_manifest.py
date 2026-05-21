#!/usr/bin/env python3
"""
================================================================================
FULL BUILD TEST MANIFEST GROUPER
================================================================================

PURPOSE:
--------
1. Reads the test runner's pipeline_summary.json to identify bugs where ALL
   commits have artifact_status == "need_full_build".
2. For each such bug, pulls the full info from fixing_commit_parent output
   (parent_info.json) — fixing commit, parent commit, test files, repo_found_in.
3. Groups bugs by their exact set of test frameworks (e.g. "mochitest",
   "xpcshell+mochitest-browser", "unknown", etc.).
4. Writes one JSON per bug inside the matching framework group folder, plus
   a group_summary.json per group and an overall_summary.json at the root.

INPUT:
------
outputs/multi_fixing_commit_build_test_runner/pipeline_summary.json
    → used to identify which bugs need full builds

outputs/fixing_commit_parent/bugs/
└── bug_<ID>/
    └── <fixing_commit>/
        └── <parent_commit>/
            └── parent_info.json

OUTPUT STRUCTURE:
-----------------
outputs/multi_fixing_full_build_test_manifest/
├── <framework_group>/                    e.g. "mochitest", "xpcshell+unknown"
│   ├── bug_<ID>.json                     one file per bug
│   └── group_summary.json               stats for this group
└── overall_summary.json                 stats across all groups
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

RUNNER_BASE      = "multi_fixing_commit_build_test_runner"
NO_PREBUILT_DIR  = "bugs_with_no_prebuilt_binaries"
NOT_VALIDATED_DIR = "bugs_with_all_prebuilt_binaries/pipeline_not_validated"
PARENT_INPUT_DIR = "fixing_commit_parent/bugs"
OUTPUT_DIR       = "multi_fixing_full_build_test_manifest"


# ===========================================================================
# STEP 1 — IDENTIFY BUGS THAT NEED FULL BUILD
# ===========================================================================

def load_full_build_bug_ids(outputs_base: Path) -> Tuple[List[str], Dict[str, str]]:
    """
    Collect bug IDs from two sources inside the test runner output:
      1. bugs_with_no_prebuilt_binaries/       — zero artifacts, need full build
      2. bugs_with_all_prebuilt_binaries/pipeline_not_validated/ — had artifacts but didn't validate

    Returns:
      - sorted list of unique bug IDs
      - dict mapping bug_id → source label (for reporting)
    """
    sources = {
        "no_prebuilt":    outputs_base / RUNNER_BASE / NO_PREBUILT_DIR,
        "not_validated":  outputs_base / RUNNER_BASE / NOT_VALIDATED_DIR,
    }

    bug_source: Dict[str, str] = {}

    for source_label, source_dir in sources.items():
        if not source_dir.exists():
            print(f"  WARNING: {source_dir} not found — skipping")
            continue
        for bug_dir in sorted(source_dir.glob("bug_*/")):
            bug_id = bug_dir.name.replace("bug_", "")
            if bug_id not in bug_source:
                bug_source[bug_id] = source_label
            else:
                # appeared in both — keep both labels
                bug_source[bug_id] = f"{bug_source[bug_id]}+{source_label}"

    no_prebuilt_count  = sum(1 for s in bug_source.values() if "no_prebuilt"   in s)
    not_validated_count = sum(1 for s in bug_source.values() if "not_validated" in s)
    print(f"  From bugs_with_no_prebuilt_binaries       : {no_prebuilt_count} bug(s)")
    print(f"  From pipeline_not_validated               : {not_validated_count} bug(s)")
    print(f"  Total unique bugs to process              : {len(bug_source)}\n")

    return sorted(bug_source.keys()), bug_source


# ===========================================================================
# STEP 2 — PULL FULL INFO FROM fixing_commit_parent
# ===========================================================================

def load_parent_info(bug_dir: Path) -> List[Dict]:
    """
    Walk bug_<ID>/<fixing_commit>/<parent_commit>/parent_info.json
    and return a list of all parent_info records found for this bug.
    Deduplicates by (fixing_commit, parent_commit) pair.
    """
    records = []
    seen    = set()

    for fixing_dir in sorted(bug_dir.iterdir()):
        if not fixing_dir.is_dir():
            continue
        fixing_commit = fixing_dir.name

        for parent_dir_path in sorted(fixing_dir.iterdir()):
            if not parent_dir_path.is_dir():
                continue

            info_path = parent_dir_path / "parent_info.json"
            if not info_path.exists():
                continue

            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"    Warning: could not read {info_path}: {e}")
                continue

            parent_commit = info.get("parent_commit")
            key = (fixing_commit, parent_commit or "no_parent")
            if key in seen:
                continue
            seen.add(key)
            records.append(info)

    return records


def build_bug_payload(bug_id: str, parent_records: List[Dict]) -> Optional[Dict]:
    """
    Combine all parent_info records for a bug into a single structured payload.
    Each record contributes one fixing+parent commit pair with its test files.
    """
    if not parent_records:
        return None

    commit_pairs  = []
    all_frameworks = set()

    for rec in parent_records:
        test_files = rec.get("test_files", [])
        frameworks = sorted(set(tf.get("framework", "unknown") for tf in test_files))
        for fw in frameworks:
            all_frameworks.add(fw)

        commit_pairs.append({
            "fixing_commit":   rec.get("fixing_commit"),
            "parent_commit":   rec.get("parent_commit"),
            "repo_found_in":   rec.get("repo_found_in"),
            "parent_metadata": rec.get("parent_metadata", {}),
            "frameworks":      frameworks,
            "test_files":      test_files,
        })

    return {
        "bug_id":          bug_id,
        "framework_group": "+".join(sorted(all_frameworks)),
        "all_frameworks":  sorted(all_frameworks),
        "total_commit_pairs":   len(commit_pairs),
        "total_test_files":     sum(len(p["test_files"]) for p in commit_pairs),
        "commit_pairs":    commit_pairs,
        "generated_at":    datetime.now().isoformat(),
    }


# ===========================================================================
# STEP 3 — GROUP BY FRAMEWORK SET
# ===========================================================================

def framework_group_label(frameworks: List[str]) -> str:
    """
    Produce a filesystem-safe folder name from the sorted framework set.
    e.g. ["mochitest", "xpcshell"] → "mochitest+xpcshell"
    Empty → "no_framework"
    """
    if not frameworks:
        return "no_framework"
    return "+".join(sorted(frameworks))


# ===========================================================================
# STEP 4 — OUTPUT WRITER
# ===========================================================================

class OutputWriter:

    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.base.mkdir(parents=True, exist_ok=True)

    def save_bug(self, group_label: str, bug_id: str, payload: Dict):
        group_dir = self.base / group_label
        group_dir.mkdir(parents=True, exist_ok=True)
        out_path = group_dir / f"bug_{bug_id}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def save_group_summary(self, group_label: str, bugs: List[Dict]):
        group_dir = self.base / group_label
        group_dir.mkdir(parents=True, exist_ok=True)

        total_test_files   = sum(b["total_test_files"]   for b in bugs)
        total_commit_pairs = sum(b["total_commit_pairs"] for b in bugs)

        summary = {
            "group":             group_label,
            "total_bugs":        len(bugs),
            "total_commit_pairs": total_commit_pairs,
            "total_test_files":  total_test_files,
            "generated_at":      datetime.now().isoformat(),
            "bugs": [
                {
                    "bug_id":            b["bug_id"],
                    "all_frameworks":    b["all_frameworks"],
                    "total_commit_pairs": b["total_commit_pairs"],
                    "total_test_files":  b["total_test_files"],
                    "commit_pairs": [
                        {
                            "fixing_commit": cp["fixing_commit"],
                            "parent_commit": cp["parent_commit"],
                            "repo_found_in": cp["repo_found_in"],
                            "frameworks":    cp["frameworks"],
                            "test_files": [
                                {
                                    "filename":     tf.get("filename", ""),
                                    "filepath":     tf.get("filepath", ""),
                                    "change_type":  tf.get("change_type", ""),
                                    "framework":    tf.get("framework", ""),
                                    "mach_command": tf.get("mach_command", ""),
                                }
                                for tf in cp["test_files"]
                            ],
                        }
                        for cp in b["commit_pairs"]
                    ],
                }
                for b in bugs
            ],
        }
        out_path = group_dir / "group_summary.json"
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"  ✓ group_summary.json → {out_path}")

    def save_overall_summary(self, groups: Dict[str, List[Dict]], total_need_build: int):
        rows = []
        grand_bugs        = 0
        grand_test_files  = 0
        grand_pairs       = 0

        for group_label, bugs in sorted(groups.items()):
            n_bugs  = len(bugs)
            n_files = sum(b["total_test_files"]   for b in bugs)
            n_pairs = sum(b["total_commit_pairs"] for b in bugs)
            grand_bugs       += n_bugs
            grand_test_files += n_files
            grand_pairs      += n_pairs
            rows.append({
                "framework_group":    group_label,
                "total_bugs":         n_bugs,
                "total_commit_pairs": n_pairs,
                "total_test_files":   n_files,
                "bug_ids":            [b["bug_id"] for b in bugs],
            })

        summary = {
            "generated_at":             datetime.now().isoformat(),
            "total_bugs_need_full_build": total_need_build,
            "total_bugs_with_parent_info": grand_bugs,
            "total_bugs_missing_parent_info": total_need_build - grand_bugs,
            "total_framework_groups":   len(groups),
            "grand_total_commit_pairs": grand_pairs,
            "grand_total_test_files":   grand_test_files,
            "groups":                   rows,
        }
        out_path = self.base / "overall_summary.json"
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n✓ overall_summary.json → {out_path}")
        return summary


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

class FullBuildManifestPipeline:

    def __init__(self):
        self.script_dir   = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"

        self.parent_input = self.outputs_base / PARENT_INPUT_DIR
        self.output_base  = self.outputs_base / OUTPUT_DIR
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.writer = OutputWriter(self.output_base)

        print(f"No-prebuilt input    : {self.outputs_base / RUNNER_BASE / NO_PREBUILT_DIR}")
        print(f"Not-validated input  : {self.outputs_base / RUNNER_BASE / NOT_VALIDATED_DIR}")
        print(f"Parent info input    : {self.parent_input}")
        print(f"Output               : {self.output_base}\n")

    def run(self):
        print("=" * 80)
        print("FULL BUILD TEST MANIFEST GROUPER")
        print("=" * 80 + "\n")

        # Step 1 — find bugs that need full build
        full_build_ids, bug_source_map = load_full_build_bug_ids(self.outputs_base)
        if not full_build_ids:
            print("No bugs to process.")
            return

        total_need_build = len(full_build_ids)

        # Step 2 & 3 — pull parent info and group by framework
        groups: Dict[str, List[Dict]] = defaultdict(list)
        missing_parent_info = []
        total = len(full_build_ids)

        for idx, bug_id in enumerate(full_build_ids, 1):
            print(f"[{idx}/{total}] Bug {bug_id} …", end=" ")

            bug_dir = self.parent_input / f"bug_{bug_id}"
            if not bug_dir.exists():
                print("✗ no parent_info directory found")
                missing_parent_info.append(bug_id)
                continue

            parent_records = load_parent_info(bug_dir)
            if not parent_records:
                print("✗ no parent_info.json files found")
                missing_parent_info.append(bug_id)
                continue

            payload = build_bug_payload(bug_id, parent_records)
            if not payload:
                print("✗ could not build payload")
                missing_parent_info.append(bug_id)
                continue

            # Tag which source this bug came from
            payload["source"] = bug_source_map.get(bug_id, "unknown")

            group_label = payload["framework_group"]
            groups[group_label].append(payload)

            # Step 4 — save individual bug JSON
            self.writer.save_bug(group_label, bug_id, payload)
            print(f"✓ group={group_label}  source={payload['source']}  pairs={payload['total_commit_pairs']}  files={payload['total_test_files']}")

        # Save per-group summaries
        print(f"\n── Writing group summaries ──────────────────────────────────")
        for group_label, bugs in sorted(groups.items()):
            self.writer.save_group_summary(group_label, bugs)

        # Save overall summary
        overall = self.writer.save_overall_summary(dict(groups), total_need_build)

        # Print summary to console
        self._print_summary(overall, missing_parent_info)

    def _print_summary(self, overall: Dict, missing: List[str]):
        print("\n" + "=" * 80)
        print("FULL BUILD MANIFEST GROUPER — SUMMARY")
        print("=" * 80)
        print(f"  Total bugs needing full build      : {overall['total_bugs_need_full_build']}")
        print(f"  Bugs with parent info found        : {overall['total_bugs_with_parent_info']}")
        print(f"  Bugs missing parent info           : {overall['total_bugs_missing_parent_info']}")
        print(f"  Framework groups created           : {overall['total_framework_groups']}")
        print(f"  Total commit pairs                 : {overall['grand_total_commit_pairs']}")
        print(f"  Total test files                   : {overall['grand_total_test_files']}")
        print(f"\n  ── Groups breakdown ────────────────────────────────────────")
        for row in sorted(overall["groups"], key=lambda x: -x["total_bugs"]):
            print(
                f"    {row['framework_group']:40s}"
                f"  bugs={row['total_bugs']:4d}"
                f"  pairs={row['total_commit_pairs']:4d}"
                f"  files={row['total_test_files']:4d}"
            )
        if missing:
            print(f"\n  Bugs with no parent info (skipped): {missing}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    pipeline = FullBuildManifestPipeline()
    pipeline.run()

    print("\n" + "=" * 80)
    print("✓  STEP COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
