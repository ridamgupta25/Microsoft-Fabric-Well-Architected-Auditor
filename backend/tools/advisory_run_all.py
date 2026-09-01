#!/usr/bin/env python3
"""Pre-label every advisory check in a run, in one command.

Runs the deterministic pre-labeler over every job in the manifest: a
check-specific labeler where one exists, otherwise the generic
unreadable-only pass. Each labels file is filled for the clear-cut cases and
left blank (NEEDS-REVIEW) for anything a rule cannot decide, so the Advisory
Judge afterwards only has to look at the rows the script could not settle.

It writes labels only. It does not score - run ``auditfast advisory-score``
after the reviewer has filled any NEEDS-REVIEW rows.

Usage, from the ``backend`` directory::

    python tools/advisory_run_all.py                 # newest run
    python tools/advisory_run_all.py --run output/<dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# Allow "python tools/advisory_run_all.py" from backend/ to import the sibling module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.advisory_prelabel import LABELERS, prelabel


def _newest_run(output_dir: Path) -> Optional[Path]:
    runs = [p for p in output_dir.iterdir() if p.is_dir() and (p / "advisory-manifest.json").exists()]
    return max(runs, key=lambda p: p.name) if runs else None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-label every advisory check in a run.")
    parser.add_argument("--run", type=Path, help="Run directory. Defaults to the newest under output/.")
    args = parser.parse_args(argv)

    run = args.run or _newest_run(Path("output"))
    if run is None or not run.exists():
        print("No run directory found under output/.", file=sys.stderr)
        return 1

    manifest_path = run / "advisory-manifest.json"
    if not manifest_path.exists():
        print(f"No advisory-manifest.json in {run}.", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    jobs = manifest.get("jobs", [])
    print(f"Pre-labeling {len(jobs)} check(s) in {run.name}\n")

    specific = generic = failed = 0
    for job in jobs:
        check_id = job["check_id"]
        job_path = Path(job["job"])
        labels_path = Path(job["labels_file"])
        if not job_path.exists() or not labels_path.exists():
            print(f"  {check_id}: SKIP (missing job/labels file)")
            failed += 1
            continue
        rc = prelabel(check_id, job_path, labels_path, verify=False)
        if rc == 0:
            if check_id in LABELERS:
                specific += 1
            else:
                generic += 1
        else:
            failed += 1

    print(
        f"\nDone. {specific} check(s) with a specific labeler, "
        f"{generic} with the generic unreadable-only pass, {failed} skipped."
    )
    print("Next: the Advisory Judge reviews the NEEDS-REVIEW rows, then run "
          "`..\\.venv\\Scripts\\python.exe -m auditfast advisory-score`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
