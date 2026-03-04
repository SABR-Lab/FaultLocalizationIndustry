#!/usr/bin/env python3
"""
================================================================================
STEP 4A: FIND FIXING & REGRESSOR COMMITS
================================================================================

PURPOSE:
--------
For each bug from Step 3 (bugs with regressed_by), find:
  - All fixing commits   → for the crashed bug itself
  - All regressor commits → for each bug listed in regressed_by

COMMIT FINDING STRATEGY:
------------------------
Fixing commits (for the crashed bug):
  TIER 1  → GET /bug/<id>/history → find RESOLVED FIXED timestamp
             GET /bug/<id>/comment → find comment nearest that timestamp
             → extract hg.mozilla.org/rev/ links
             → stop here if any links found (timing is already precise)

  TIER 2  → scan only comments posted AT OR AFTER the RESOLVED FIXED timestamp
             (pre-resolution comments are bisection notes / review discussion,
             not the actual fix link)
             → verify each hash: bug ID must appear in commit message
               * check local repos first
               * if not local → query hg.mozilla.org/json-rev/<hash> remotely
               * only keep if confirmed; drop if confirmed wrong; keep if
                 both local and remote lookups fail

  TIER 3  → hg log -k "Bug <id>" across local repos
             (bug ID verified in commit message by construction)

Regressor commits (for each regressed_by bug):
  TIER 1  → GET /bug/<crashed_bug_id>/history → find when regressed_by field
             was SET → nearest comment → hg links → verify against reg bug ID
             using same two-step local+remote verification

  TIER 2  → GET /bug/<regressed_by_id>/comment → scan all comments
             → verify each hash: reg bug ID must appear in commit message

  TIER 3  → scan comments of crashed bug → verify against reg bug ID

  TIER 4  → hg log -k "Bug <reg_id>" across local repos

OUTPUT:
-------
outputs/multi_commit_extraction/
└── bug_<ID>/
    ├── fixing_commits.json      (commits sorted newest → oldest)
    └── regressor_commits.json
outputs/multi_commit_extraction/
├── pipeline_summary.json
└── statistics_report.txt
"""

import json
import os
import re
import subprocess
import sys
import time
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

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

BUGZILLA_API = "https://bugzilla.mozilla.org/rest"
HG_BASE      = "https://hg.mozilla.org"

LOCAL_REPOS = {
    "mozilla-central":  "./mozilla-central",
    "mozilla-autoland": "./mozilla-autoland",
    "mozilla-release":  "./mozilla-release",
    "mozilla-esr115":   "./mozilla-esr115",
}

# Repos to try when doing remote commit message lookup (json-rev)
HG_REMOTE_REPOS = [
    "mozilla-central",
    "integration/autoland",
    "releases/mozilla-esr128",
    "releases/mozilla-esr115",
]

HG_REV_RE = re.compile(
    r'https://hg\.mozilla\.org/([^/\s"\'<>]+(?:/[^/\s"\'<>]+)*)'
    r'/rev/([0-9a-f]{7,40})',
    re.IGNORECASE,
)

BUG_ID_PATTERNS = [
    r'[Bb]ug\s+{bid}',
    r'b={bid}',
    r'[Bb]ug[:\-]?\s*{bid}',
    r'\[Bug\s*{bid}\]',
]

API_DELAY        = 0.35   # seconds between Bugzilla REST calls
REMOTE_VER_DELAY = 0.20   # seconds between hg.mozilla.org json-rev calls


# ===========================================================================
# HELPERS
# ===========================================================================

def bug_appears_in_message(bug_id: str, message: str) -> bool:
    """Return True if bug_id is referenced in a commit message."""
    for pat in BUG_ID_PATTERNS:
        if re.search(pat.format(bid=re.escape(bug_id)), message):
            return True
    return False


def extract_hg_links(text: str) -> List[Tuple[str, str]]:
    """
    Extract (repo_path, commit_hash) pairs from free text.
    Deduplicated by commit hash (first occurrence wins).
    """
    seen, result = set(), []
    for repo, rev in HG_REV_RE.findall(text):
        if rev not in seen:
            seen.add(rev)
            result.append((repo, rev))
    return result


def comments_at_or_after(comments: List[dict], iso_timestamp: str) -> List[dict]:
    """Return comments whose creation_time >= iso_timestamp."""
    return [c for c in comments if c.get("creation_time", "") >= iso_timestamp]


def comment_closest_to(
    comments: List[dict], iso_timestamp: str
) -> Optional[dict]:
    """
    Return the comment whose creation_time is closest to (and at or after)
    iso_timestamp. Falls back to the most recent comment before it.
    """
    if not comments or not iso_timestamp:
        return None
    after  = [c for c in comments if c.get("creation_time", "") >= iso_timestamp]
    before = [c for c in comments if c.get("creation_time", "") <  iso_timestamp]
    if after:
        return min(after, key=lambda c: c.get("creation_time", ""))
    if before:
        return max(before, key=lambda c: c.get("creation_time", ""))
    return None


def sort_commits_newest_first(commits: List[Dict]) -> List[Dict]:
    """Sort commit dicts newest → oldest by pushdate."""
    return sorted(commits, key=lambda c: c.get("pushdate", ""), reverse=True)


# ===========================================================================
# LOCAL REPO MANAGER
# ===========================================================================

class LocalRepoManager:
    """
    Wraps local Mercurial repositories for:
      1. Verifying a commit hash and reading its message + metadata
      2. Finding commits by bug ID via hg log -k  (tier-3/4 fallback)
    """

    def __init__(self):
        self.available: Dict[str, str] = {}
        print("Local Mercurial repositories:")
        for name, path in LOCAL_REPOS.items():
            if os.path.isdir(path):
                self.available[name] = path
                print(f"  ✓  {name}: {path}")
            else:
                print(f"  ✗  {name}: {path} (not found)")
        print()

    def get_commit_info(self, commit_hash: str) -> Optional[Dict]:
        """
        Return {node, author, pushdate, desc, repo_name} for commit_hash,
        or None if not found in any local repo.
        """
        for repo_name, repo_path in self.available.items():
            try:
                r = subprocess.run(
                    [
                        "hg", "log", "-r", commit_hash,
                        "--template",
                        "{node}\\n{author}\\n{date|isodate}\\n{desc|firstline}",
                    ],
                    cwd=repo_path,
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0 and r.stdout.strip():
                    lines = r.stdout.strip().split("\n")
                    if len(lines) >= 4:
                        return {
                            "node":      lines[0].strip(),
                            "author":    lines[1].strip(),
                            "pushdate":  lines[2].strip(),
                            "desc":      lines[3].strip(),
                            "repo_name": repo_name,
                        }
            except Exception:
                continue
        return None

    def find_commits_by_bug_id(self, bug_id: str) -> List[Dict]:
        """
        Search all available local repos for commits mentioning bug_id.
        Returns deduplicated list, sorted newest first.
        """
        commits, seen = [], set()

        for repo_name, repo_path in self.available.items():
            try:
                r = subprocess.run(
                    [
                        "hg", "log",
                        "-k", f"Bug {bug_id}",
                        "--template",
                        "{node}\\n{author}\\n{date|isodate}\\n"
                        "{desc|firstline}\\n---END---\\n",
                    ],
                    cwd=repo_path,
                    capture_output=True, text=True, timeout=30,
                )
                if r.returncode != 0 or not r.stdout.strip():
                    continue

                for entry in r.stdout.strip().split("---END---\n"):
                    entry = entry.strip()
                    if not entry:
                        continue
                    lines = entry.split("\n")
                    if len(lines) < 4:
                        continue

                    node = lines[0].strip()
                    desc = lines[3].strip()

                    if not bug_appears_in_message(bug_id, desc):
                        continue
                    if node in seen:
                        continue
                    seen.add(node)

                    commits.append({
                        "node":      node,
                        "desc":      desc,
                        "author":    lines[1].strip(),
                        "pushdate":  lines[2].strip(),
                        "repo_name": repo_name,
                        "source":    f"local:{repo_name}",
                    })

            except subprocess.TimeoutExpired:
                print(f"    [local] timeout in {repo_name} for bug {bug_id}")
            except Exception as e:
                print(f"    [local] error in {repo_name}: {e}")

        return commits


# ===========================================================================
# REMOTE COMMIT VERIFIER
# ===========================================================================

class RemoteCommitVerifier:
    """
    Fetches commit metadata from hg.mozilla.org/json-rev/<hash> to verify
    a commit message when the hash is not present in any local repo.

    This is the key improvement over the previous version: instead of
    blindly keeping unverifiable hashes, we actively check remotely.
    """

    def __init__(self, delay: float = REMOTE_VER_DELAY):
        self.delay   = delay
        self.session = requests.Session()
        self.session.headers.update({
            "Accept":     "application/json",
            "User-Agent": "Mozilla-Crash-Analysis-Research/1.0",
        })
        # Cache: commit_hash → desc string (or None if not found anywhere)
        self._cache: Dict[str, Optional[str]] = {}

    def get_commit_desc(self, commit_hash: str) -> Optional[str]:
        """
        Return the first line of the commit description, or None if not
        found in any remote repo.  Results are cached.
        """
        if commit_hash in self._cache:
            return self._cache[commit_hash]

        for repo in HG_REMOTE_REPOS:
            time.sleep(self.delay)
            url = f"{HG_BASE}/{repo}/json-rev/{commit_hash}"
            try:
                r = self.session.get(url, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    desc = data.get("desc", "").split("\n")[0].strip()
                    self._cache[commit_hash] = desc
                    return desc
            except Exception:
                continue

        self._cache[commit_hash] = None
        return None


# ===========================================================================
# BUGZILLA CLIENT
# ===========================================================================

class BugzillaClient:

    def __init__(self, delay: float = API_DELAY):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            "Accept":     "application/json",
            "User-Agent": "Mozilla-Crash-Analysis-Research/1.0",
        })
        self._comment_cache: Dict[str, List[dict]] = {}

    def _get(self, url: str, params: dict = None) -> Optional[dict]:
        time.sleep(self.delay)
        try:
            r = self.session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"    [Bugzilla] {url} → {e}")
            return None

    def get_history(self, bug_id: str) -> List[dict]:
        data = self._get(f"{BUGZILLA_API}/bug/{bug_id}/history")
        if not data:
            return []
        return data.get("bugs", [{}])[0].get("history", [])

    def get_comments(self, bug_id: str) -> List[dict]:
        """Fetch comments with per-session caching."""
        if bug_id in self._comment_cache:
            return self._comment_cache[bug_id]
        data = self._get(f"{BUGZILLA_API}/bug/{bug_id}/comment")
        if not data:
            comments = []
        else:
            comments = (
                data.get("bugs", {})
                    .get(str(bug_id), {})
                    .get("comments", [])
            )
        self._comment_cache[bug_id] = comments
        return comments

    def get_resolved_fixed_time(self, bug_id: str) -> Optional[str]:
        """ISO timestamp of first RESOLVED FIXED event, or None."""
        for event in self.get_history(bug_id):
            changes       = event.get("changes", [])
            status_ok     = any(
                c.get("field_name") == "status"
                and c.get("added", "").upper() == "RESOLVED"
                for c in changes
            )
            resolution_ok = any(
                c.get("field_name") == "resolution"
                and c.get("added", "").upper() == "FIXED"
                for c in changes
            )
            if status_ok and resolution_ok:
                return event.get("when")
        return None

    def get_regressed_by_set_time(self, bug_id: str) -> Optional[str]:
        """
        ISO timestamp of the first event where regressed_by was set.
        This is when devs paste mozregression output.
        """
        for event in self.get_history(bug_id):
            for change in event.get("changes", []):
                if (
                    change.get("field_name") == "regressed_by"
                    and change.get("added", "").strip()
                ):
                    return event.get("when")
        return None


# ===========================================================================
# COMMIT FINDER
# ===========================================================================

class CommitFinder:
    """
    Multi-tier commit finder.  Each tier is only reached if the previous
    tier found nothing.
    """

    def __init__(
        self,
        bz:     BugzillaClient,
        local:  LocalRepoManager,
        remote: RemoteCommitVerifier,
    ):
        self.bz     = bz
        self.local  = local
        self.remote = remote

    # ------------------------------------------------------------------
    # Fixing commits
    # ------------------------------------------------------------------

    def find_fixing_commits(
        self, bug_id: str
    ) -> Tuple[List[Dict], str]:
        """Returns (commits_sorted_newest_first, tier_label)."""

        resolved_time = self.bz.get_resolved_fixed_time(bug_id)
        comments      = self.bz.get_comments(bug_id)   # cached; reused below

        # T1 — history → RESOLVED FIXED → nearest comment
        #      No verification needed; timing correlation is precise.
        if resolved_time:
            nearest = comment_closest_to(comments, resolved_time)
            if nearest:
                links = extract_hg_links(nearest.get("text", ""))
                if links:
                    commits = self._enrich_links(links, "bugzilla_comment")
                    if commits:
                        print(f"      [fixing T1] → {len(commits)} commit(s)")
                        return sort_commits_newest_first(commits), "T1_history_comment"

        # T2 — only comments posted AT OR AFTER resolved time, verified.
        #      Restricting to post-resolution comments eliminates bisection
        #      notes and review discussion that precede the actual fix link.
        post_comments = (
            comments_at_or_after(comments, resolved_time)
            if resolved_time
            else comments          # no resolved time → scan all
        )
        links = self._verified_links_from_comments(post_comments, bug_id)
        if links:
            commits = self._enrich_links(links, "bugzilla_comment")
            if commits:
                label = "T2_post_resolution_comments"
                print(f"      [fixing T2] {label} → {len(commits)} commit(s)")
                return sort_commits_newest_first(commits), label

        # T3 — local repos
        local_commits = self.local.find_commits_by_bug_id(bug_id)
        if local_commits:
            commits = [self._local_to_commit(c) for c in local_commits]
            print(f"      [fixing T3] local repos → {len(commits)} commit(s)")
            return sort_commits_newest_first(commits), "T3_local_repo"

        print(f"      [fixing] not found")
        return [], "not_found"

    # ------------------------------------------------------------------
    # Regressor commits
    # ------------------------------------------------------------------

    def find_regressor_commits(
        self, reg_bug_id: str, crashed_bug_id: str
    ) -> Tuple[List[Dict], str]:
        """Returns (commits_sorted_newest_first, tier_label)."""

        reg_set_time     = self.bz.get_regressed_by_set_time(crashed_bug_id)
        crashed_comments = self.bz.get_comments(crashed_bug_id)  # cached

        # T1 — when was regressed_by field set on the crashed bug?
        if reg_set_time:
            nearest = comment_closest_to(crashed_comments, reg_set_time)
            if nearest:
                links = extract_hg_links(nearest.get("text", ""))
                links = self._verify_links(links, reg_bug_id)
                if links:
                    commits = self._enrich_links(links, "bugzilla_comment")
                    if commits:
                        print(f"      [regressor T1] regressed_by set → {len(commits)} commit(s)")
                        return sort_commits_newest_first(commits), "T1_regressed_by_set_comment"

        # T2 — regressor bug's own comments, verified
        reg_comments = self.bz.get_comments(reg_bug_id)
        links = self._verified_links_from_comments(reg_comments, reg_bug_id)
        if links:
            commits = self._enrich_links(links, "bugzilla_comment")
            if commits:
                print(f"      [regressor T2] verified reg-bug comments → {len(commits)} commit(s)")
                return sort_commits_newest_first(commits), "T2_verified_regressor_comments"

        # T3 — crashed bug's comments, verified against regressor bug ID
        links = self._verified_links_from_comments(crashed_comments, reg_bug_id)
        if links:
            commits = self._enrich_links(links, "bugzilla_comment")
            if commits:
                print(f"      [regressor T3] verified crashed-bug comments → {len(commits)} commit(s)")
                return sort_commits_newest_first(commits), "T3_verified_crashed_comments"

        # T4 — local repos
        local_commits = self.local.find_commits_by_bug_id(reg_bug_id)
        if local_commits:
            commits = [self._local_to_commit(c) for c in local_commits]
            print(f"      [regressor T4] local repos → {len(commits)} commit(s)")
            return sort_commits_newest_first(commits), "T4_local_repo"

        print(f"      [regressor {reg_bug_id}] not found")
        return [], "not_found"

    # ------------------------------------------------------------------
    # Verification  (the noise filter)
    # ------------------------------------------------------------------

    def _verified_links_from_comments(
        self, comments: List[dict], bug_id: str
    ) -> List[Tuple[str, str]]:
        """Extract all hg links from comments, keep only verified ones."""
        all_text  = "\n".join(c.get("text", "") for c in comments)
        raw_links = extract_hg_links(all_text)
        return self._verify_links(raw_links, bug_id)

    def _verify_links(
        self, links: List[Tuple[str, str]], bug_id: str
    ) -> List[Tuple[str, str]]:
        """
        Three-outcome verification for each (hint_repo, hash):

          1. Local repo has the hash → check message → keep or drop.
          2. Not local → query hg.mozilla.org/json-rev/<hash> remotely
               → check message → keep or drop.
          3. Both lookups failed (network error / very old commit) → keep
             (last-resort; we genuinely cannot verify).
        """
        verified = []
        for hint_repo, commit_hash in links:
            short = commit_hash[:12]

            # Step 1: local lookup
            info = self.local.get_commit_info(commit_hash)
            if info is not None:
                if bug_appears_in_message(bug_id, info["desc"]):
                    verified.append((hint_repo, commit_hash))
                else:
                    print(
                        f"        [verify local] dropped {short} "
                        f"— bug {bug_id} not in: \"{info['desc'][:70]}\""
                    )
                continue   # local lookup conclusive either way

            # Step 2: remote lookup via json-rev
            print(f"        [verify remote] {short} not local — querying hg.mozilla.org …")
            remote_desc = self.remote.get_commit_desc(commit_hash)
            if remote_desc is not None:
                if bug_appears_in_message(bug_id, remote_desc):
                    print(f"        [verify remote] {short} confirmed ✓")
                    verified.append((hint_repo, commit_hash))
                else:
                    print(
                        f"        [verify remote] dropped {short} "
                        f"— bug {bug_id} not in: \"{remote_desc[:70]}\""
                    )
                continue   # remote lookup conclusive

            # Step 3: both failed — keep with warning
            print(
                f"        [verify] {short} unverifiable (not local, "
                f"not remote) — keeping as fallback"
            )
            verified.append((hint_repo, commit_hash))

        return verified

    # ------------------------------------------------------------------
    # Enrichment helpers
    # ------------------------------------------------------------------

    def _enrich_links(
        self, links: List[Tuple[str, str]], source: str
    ) -> List[Dict]:
        """
        Convert (hint_repo, hash) pairs into full commit dicts.
        Tries local first, then remote json-rev for metadata.
        """
        commits = []
        seen    = set()

        for hint_repo, commit_hash in links:
            if commit_hash in seen:
                continue
            seen.add(commit_hash)

            # Local metadata
            info = self.local.get_commit_info(commit_hash)
            if info:
                commits.append({
                    "commit_hash": info["node"],
                    "short_hash":  info["node"][:12],
                    "description": info["desc"],
                    "author":      info["author"],
                    "pushdate":    info["pushdate"],
                    "hint_repo":   hint_repo,
                    "source":      source,
                })
                continue

            # Remote metadata via json-rev
            remote_desc = self.remote.get_commit_desc(commit_hash)
            commits.append({
                "commit_hash": commit_hash,
                "short_hash":  commit_hash[:12],
                "description": remote_desc or "",
                "author":      "",
                "pushdate":    "",
                "hint_repo":   hint_repo,
                "source":      source,
            })

        return commits

    @staticmethod
    def _local_to_commit(c: Dict) -> Dict:
        return {
            "commit_hash": c["node"],
            "short_hash":  c["node"][:12],
            "description": c["desc"],
            "author":      c["author"],
            "pushdate":    c["pushdate"],
            "hint_repo":   c.get("repo_name", ""),
            "source":      c.get("source", "local"),
        }


# ===========================================================================
# OUTPUT WRITER
# ===========================================================================

class OutputWriter:

    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.base.mkdir(parents=True, exist_ok=True)

    def save_fixing_commits(
        self, bug_id: str, commits: List[Dict], method: str
    ):
        bug_dir = self.base / f"bug_{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "bug_id":        bug_id,
            "find_method":   method,
            "total_commits": len(commits),
            "commits":       commits,
        }
        (bug_dir / "fixing_commits.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def save_regressor_commits(
        self,
        bug_id:       str,
        regressed_by: List[str],
        regressors:   List[Dict],
    ):
        bug_dir = self.base / f"bug_{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "bug_id":               bug_id,
            "regressed_by":         regressed_by,
            "total_regressor_bugs": len(regressors),
            "regressors":           regressors,
        }
        (bug_dir / "regressor_commits.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

class Step4aPipeline:

    def __init__(self, rate_limit: float = API_DELAY):
        self.script_dir   = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"

        self.input_dir = (
            self.outputs_base
            / "step3_regressed_by_filter"
            / "bugs_with_regression"
            / "bugs"
        )
        self.output_base = self.outputs_base / "multi_commit_extraction"
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.local  = LocalRepoManager()
        self.bz     = BugzillaClient(delay=rate_limit)
        self.remote = RemoteCommitVerifier()
        self.finder = CommitFinder(self.bz, self.local, self.remote)
        self.writer = OutputWriter(self.output_base)

        print(f"Input:  {self.input_dir}")
        print(f"Output: {self.output_base}\n")

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_bugs(self) -> Dict[str, Dict]:
        bugs = {}
        if not self.input_dir.exists():
            print(f"ERROR: input directory not found: {self.input_dir}")
            return bugs
        for fp in self.input_dir.glob("bug_*.json"):
            try:
                data = json.loads(fp.read_text())
                bid  = str(data.get("bug_id", ""))
                if bid:
                    bugs[bid] = data
            except Exception as e:
                print(f"  Warning: {fp.name}: {e}")
        return bugs

    # ------------------------------------------------------------------
    # Per-bug processing
    # ------------------------------------------------------------------

    def process_bug(
        self, bug_id: str, bug_data: Dict, idx: int, total: int
    ) -> Dict:
        print(f"\n[{idx}/{total}] Bug {bug_id}")
        regressed_by = [str(r) for r in bug_data.get("regressed_by", [])]
        print(f"    regressed_by: {regressed_by}")

        # Fixing
        print(f"    Finding fixing commits …")
        fixing_commits, fixing_method = self.finder.find_fixing_commits(bug_id)
        self.writer.save_fixing_commits(bug_id, fixing_commits, fixing_method)
        print(f"    → {len(fixing_commits)} fixing commit(s) [{fixing_method}]")

        # Regressors
        regressors = []
        for reg_bug_id in regressed_by:
            print(f"    Finding regressor commits for bug {reg_bug_id} …")
            reg_commits, reg_method = self.finder.find_regressor_commits(
                reg_bug_id, bug_id
            )
            regressors.append({
                "regressor_bug_id": reg_bug_id,
                "find_method":      reg_method,
                "total_commits":    len(reg_commits),
                "commits":          reg_commits,
            })
            print(f"    → {len(reg_commits)} regressor commit(s) [{reg_method}]")

        self.writer.save_regressor_commits(bug_id, regressed_by, regressors)

        return {
            "bug_id":                 bug_id,
            "fixing_commit_count":    len(fixing_commits),
            "fixing_method":          fixing_method,
            "regressor_bugs":         len(regressors),
            "regressor_commit_count": sum(r["total_commits"] for r in regressors),
            "has_fixing":             len(fixing_commits) > 0,
            "has_regressors":         any(r["total_commits"] > 0 for r in regressors),
        }

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> Dict:
        print("=" * 80)
        print("STEP 4A: FIND FIXING & REGRESSOR COMMITS")
        print("=" * 80 + "\n")

        all_bugs = self.load_bugs()
        if not all_bugs:
            print("No bugs found — check Step 3 output.")
            return {}

        total = len(all_bugs)
        print(f"Loaded {total} bugs from Step 3\n")

        stats = {
            "total_bugs":                    total,
            "bugs_with_fixing_commits":      0,
            "bugs_with_regressor_commits":   0,
            "bugs_with_both":                0,
            "bugs_no_fixing":                0,
            "bugs_no_regressor":             0,
            "total_fixing_commits_found":    0,
            "total_regressor_commits_found": 0,
            "fixing_method_counts":          defaultdict(int),
        }

        all_results = {}

        for idx, (bug_id, bug_data) in enumerate(all_bugs.items(), 1):
            try:
                res = self.process_bug(bug_id, bug_data, idx, total)
                all_results[bug_id] = res

                stats["bugs_with_fixing_commits"]    += int(res["has_fixing"])
                stats["bugs_no_fixing"]              += int(not res["has_fixing"])
                stats["bugs_with_regressor_commits"] += int(res["has_regressors"])
                stats["bugs_no_regressor"]           += int(not res["has_regressors"])
                stats["bugs_with_both"]              += int(
                    res["has_fixing"] and res["has_regressors"]
                )
                stats["total_fixing_commits_found"]    += res["fixing_commit_count"]
                stats["total_regressor_commits_found"] += res["regressor_commit_count"]
                stats["fixing_method_counts"][res["fixing_method"]] += 1

            except Exception as e:
                print(f"    ERROR processing bug {bug_id}: {e}")
                all_results[bug_id] = {
                    "bug_id": bug_id, "status": "error", "error": str(e)
                }

        stats["fixing_method_counts"] = dict(stats["fixing_method_counts"])

        self._print_summary(stats)
        self._save_summary(stats, all_results)
        return {"stats": stats, "results": all_results}

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(self, s: Dict):
        print("\n" + "=" * 80)
        print("STEP 4A SUMMARY")
        print("=" * 80)
        print(f"  Total bugs processed            : {s['total_bugs']}")
        print(f"  Bugs with fixing commits        : {s['bugs_with_fixing_commits']}")
        print(f"  Bugs with regressor commits     : {s['bugs_with_regressor_commits']}")
        print(f"  Bugs with BOTH                  : {s['bugs_with_both']}")
        print(f"  Bugs with NO fixing commit      : {s['bugs_no_fixing']}")
        print(f"  Bugs with NO regressor commit   : {s['bugs_no_regressor']}")
        print(f"  Total fixing commits found      : {s['total_fixing_commits_found']}")
        print(f"  Total regressor commits found   : {s['total_regressor_commits_found']}")
        print(f"\n  Fixing commit find methods:")
        for m, c in s["fixing_method_counts"].items():
            print(f"    {m:50s}: {c}")

    def _save_summary(self, stats: Dict, results: Dict):
        summary = {
            "pipeline_timestamp": datetime.now().isoformat(),
            "statistics":         stats,
            "per_bug":            results,
        }
        sp = self.output_base / "pipeline_summary.json"
        sp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n✓ pipeline_summary.json → {sp}")

        rp = self.output_base / "statistics_report.txt"
        lines = [
            "=" * 80, "STEP 4A STATISTICS REPORT", "=" * 80,
            f"Generated: {datetime.now().isoformat()}", "",
            f"Total bugs processed            : {stats['total_bugs']}",
            f"Bugs with fixing commits        : {stats['bugs_with_fixing_commits']}",
            f"Bugs with regressor commits     : {stats['bugs_with_regressor_commits']}",
            f"Bugs with BOTH                  : {stats['bugs_with_both']}",
            f"Bugs with NO fixing commit      : {stats['bugs_no_fixing']}",
            f"Bugs with NO regressor commit   : {stats['bugs_no_regressor']}",
            f"Total fixing commits found      : {stats['total_fixing_commits_found']}",
            f"Total regressor commits found   : {stats['total_regressor_commits_found']}",
            "", "Fixing commit find methods:",
        ]
        for m, c in stats["fixing_method_counts"].items():
            lines.append(f"  {m:50s}: {c}")
        lines += ["", "=" * 80, "PER-BUG RESULTS", "=" * 80, ""]
        for bid, res in results.items():
            if "error" in res:
                lines.append(f"Bug {bid}  [ERROR: {res['error']}]")
            else:
                lines.append(
                    f"Bug {bid}  "
                    f"fixing={res['fixing_commit_count']} [{res['fixing_method']}]  "
                    f"regressors={res['regressor_commit_count']}"
                )
            lines.append("")

        rp.write_text("\n".join(lines), encoding="utf-8")
        print(f"✓ statistics_report.txt  → {rp}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Find fixing & regressor commits for each bug"
    )
    parser.add_argument(
        "--rate-limit", type=float, default=API_DELAY,
        help=f"Seconds between Bugzilla API calls (default: {API_DELAY})"
    )
    args = parser.parse_args()

    pipeline = Step4aPipeline(rate_limit=args.rate_limit)
    pipeline.run()

    print("\n" + "=" * 80)
    print("✓  STEP COMPLETE")
    print("=" * 80)
    print(f"\nNext step: run step4b_download_diffs.py")


if __name__ == "__main__":
    main()
