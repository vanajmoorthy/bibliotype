#!/usr/bin/env python3
"""Parse the triage PRD markdown and emit prd.json for Ralph.

Each story in the PRD is bounded by a `#### US-XXX:` heading. Within each
story we extract:
  - id (US-XXX)
  - title (rest of the heading)
  - description (text under **Description:**)
  - acceptance criteria (bullets under **Acceptance Criteria:**)

Files / Risk-note / Test-command sections are appended to the description so
Ralph still sees them but doesn't trip on the AC parser.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List


PRD_PATH = Path(__file__).resolve().parents[2] / "tasks" / "prd-codebase-triage-fixes.md"
OUT_PATH = Path(__file__).resolve().parent / "prd.json"

STORY_HEADER = re.compile(r"^####\s+US-([\w\-]+):\s+(.*)$")


def split_stories(md: str) -> List[List[str]]:
    """Return list of line-buffers, one per story."""
    lines = md.splitlines()
    out: List[List[str]] = []
    cur: List[str] | None = None
    for line in lines:
        if STORY_HEADER.match(line):
            if cur is not None:
                out.append(cur)
            cur = [line]
        elif cur is not None:
            # Stop the current story when we hit another phase heading or the
            # appendix sections so we don't accidentally absorb them.
            if line.startswith("### Phase ") or line.startswith("## "):
                out.append(cur)
                cur = None
            else:
                cur.append(line)
    if cur is not None:
        out.append(cur)
    return out


def parse_story(buf: List[str]) -> dict:
    header = buf[0]
    m = STORY_HEADER.match(header)
    assert m, f"bad header: {header!r}"
    sid = f"US-{m.group(1)}"
    title = m.group(2).strip()

    body = buf[1:]
    text = "\n".join(body)

    # Description = the line after **Description:** until the next ** marker.
    desc_match = re.search(
        r"\*\*Description:\*\*\s*(.+?)(?=\n\s*\*\*|\n---|\Z)",
        text,
        flags=re.DOTALL,
    )
    description = desc_match.group(1).strip() if desc_match else ""

    # Files block (kept for context).
    files_match = re.search(
        r"\*\*Files:?\*\*\s*(.+?)(?=\n\s*\*\*Acceptance Criteria|\n---|\Z)",
        text,
        flags=re.DOTALL,
    )
    files_block = files_match.group(1).strip() if files_match else ""

    # Acceptance criteria: top-level `- [ ]` items only.
    ac_section_match = re.search(
        r"\*\*Acceptance Criteria:\*\*\s*(.+?)(?=\n---|\Z)",
        text,
        flags=re.DOTALL,
    )
    criteria: List[str] = []
    if ac_section_match:
        ac_text = ac_section_match.group(1)
        # Match bullets that start the line (no leading whitespace) starting with `- [ ]` or `- [x]`.
        for line in ac_text.splitlines():
            stripped = line.rstrip()
            mm = re.match(r"^- \[[ xX]\]\s+(.*)$", stripped)
            if mm:
                criteria.append(mm.group(1).strip())
            elif criteria and re.match(r"^\s+- \[[ xX]\]\s+(.*)$", stripped):
                # nested checkbox — append to last as continuation
                inner = re.match(r"^\s+- \[[ xX]\]\s+(.*)$", stripped).group(1).strip()
                criteria[-1] += f" / {inner}"
            elif criteria and re.match(r"^\s+- \s*(.*)$", stripped):
                # nested non-checkbox bullet, attach to last AC
                inner = re.match(r"^\s+- \s*(.*)$", stripped).group(1).strip()
                if inner:
                    criteria[-1] += f" — {inner}"
            elif criteria and stripped.startswith("  ") and stripped.strip():
                criteria[-1] += f" {stripped.strip()}"

    # Always include a quality gate as the final criterion (Ralph convention).
    if not any("manage.py test" in c.lower() or "tests pass" in c.lower() for c in criteria):
        criteria.append("manage.py test passes (full suite)")

    # Build the description sent to Ralph: original description + files context.
    full_description = description
    if files_block:
        full_description += f"\n\n**Files:** {files_block}"

    return {
        "id": sid,
        "title": title,
        "description": full_description.strip(),
        "acceptanceCriteria": criteria,
        "passes": False,
        "notes": "",
    }


def main() -> int:
    md = PRD_PATH.read_text()
    bufs = split_stories(md)
    stories = [parse_story(b) for b in bufs]

    # Assign numeric priority by document order so Ralph executes top-to-bottom.
    for i, s in enumerate(stories, start=1):
        s["priority"] = i

    payload = {
        "project": "Bibliotype",
        "branchName": "triage/codebase-fixes",
        "description": (
            "Codebase triage fixes from multi-agent review: "
            "critical security, performance, dead code, simplification, "
            "patterns, architecture splits."
        ),
        "userStories": stories,
    }

    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {OUT_PATH} with {len(stories)} stories.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
