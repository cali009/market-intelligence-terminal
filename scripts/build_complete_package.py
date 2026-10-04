#!/usr/bin/env python3
"""
Builds a single self-contained Markdown file containing the entire package:
every design document, followed by every source file.

Reproducible: running it again regenerates the same document from the current tree,
so the artifact can be refreshed rather than hand-maintained.

Usage:
    python3 scripts/build_complete_package.py [--out PATH]
"""

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "MARKET_INTEL_PLATFORM_COMPLETE_PACKAGE.md"

# Markdown language hint per extension.
LANG = {".py": "python", ".html": "html", ".js": "javascript", ".css": "css",
        ".sh": "bash", ".json": "json", ".yml": "yaml", ".yaml": "yaml",
        ".sql": "sql", ".md": "markdown", ".txt": "text"}

# Directories to walk, in the order they should appear.
CODE_GROUPS = [
    ("Application Source", "src"),
    ("Test Suite", "tests"),
    ("Operational Scripts", "scripts"),
    ("Terminal Front End", "web"),
    ("Configuration & Deployment", None),   # handled specially
]

CODE_EXTENSIONS = {".py", ".html", ".js", ".css", ".sh", ".sql"}

# Top-level config/deploy files worth including.
CONFIG_PATTERNS = ["*.toml", "*.cfg", "*.ini", "*.yml", "*.yaml", "Dockerfile*",
                   "requirements*.txt", ".github/workflows/*.yml"]


def git(*args) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                             text=True, timeout=20)
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def fence_for(path: Path) -> str:
    return LANG.get(path.suffix, "")


def slug(text: str) -> str:
    """
    Anchor id for a path: lowercase, separators to hyphens, dots kept.

    Dots and slashes are preserved (slashes become hyphens) rather than dropped, so the id
    stays readable and `src/a/b.py` cannot collide with `src/ab.py`.
    """
    out = []
    for ch in text.lower():
        if ch.isalnum() or ch in "-_.":
            out.append(ch)
        elif ch in " /":
            out.append("-")
    return "".join(out)


def collect_code_files():
    """Returns [(group_label, relative_path)] in display order."""
    collected = []
    for label, subdir in CODE_GROUPS:
        if subdir is None:
            files = []
            for pat in CONFIG_PATTERNS:
                files.extend(p for p in ROOT.glob(pat) if p.is_file())
            files = sorted(set(files))
        else:
            base = ROOT / subdir
            if not base.exists():
                continue
            files = sorted(p for p in base.rglob("*")
                           if p.is_file() and p.suffix in CODE_EXTENSIONS)
        for f in files:
            collected.append((label, f.relative_to(ROOT)))
    return collected


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUTPUT))
    args = ap.parse_args()
    out_path = Path(args.out)

    docs = sorted((ROOT / "docs").glob("*.md"))
    code = collect_code_files()
    now = datetime.now(timezone.utc)
    commit = git("rev-parse", "HEAD")[:12]
    branch = git("rev-parse", "--abbrev-ref", "HEAD")

    doc_lines = sum(len(read(d).splitlines()) for d in docs)
    code_lines = sum(len(read(ROOT / p).splitlines()) for _, p in code)

    parts = []
    w = parts.append

    # ---------------------------------------------------------------- front matter
    w(f"""# Market Intelligence Platform — Complete Package

**US + Canada AI-Powered Market Intelligence & Decision-Support System**

| | |
|---|---|
| Generated | {now.strftime('%Y-%m-%d %H:%M UTC')} |
| Repository | `cali009/market-intelligence-terminal` |
| Branch / commit | `{branch}` @ `{commit}` |
| Design documents | {len(docs)} ({doc_lines:,} lines) |
| Source files | {len(code)} ({code_lines:,} lines) |
| Total | {doc_lines + code_lines:,} lines |

---

## What this document is

A single self-contained artifact holding the entire package: **Part I** is the complete
design and specification set, **Part II** is the complete source code. Nothing here is
summarised or abridged — every document and every file is reproduced in full.

## What this system is not

This is **decision-support and research tooling**, not a profit-generating system and not
investment advice. Every signal carries its reasoning, confidence, assumptions, risks and
supporting data. Market data and news are never fabricated. All execution is **paper
simulation only** — no order in this system routes to a live venue.

## How to read it

- **Part I** is ordered by document number, which is also roughly the order the design
  evolved: product vision → architecture → signals & risk → roadmap → engine designs →
  the Phase 34 execution gateway specification.
- **Part II** is grouped by directory. Each file is preceded by its path and line count.
- Section anchors follow the file paths, so a path can be searched directly.

---

## Contents

### Part I — Design & Specification
""")
    for d in docs:
        w(f"- [{d.name}](#{slug(d.name)})")

    w("\n### Part II — Source Code\n")
    current = None
    for label, rel in code:
        if label != current:
            current = label
            w(f"\n**{label}**\n")
        w(f"- [`{rel}`](#{slug(str(rel))})")

    # ---------------------------------------------------------------- Part I
    w("""

---
---

# PART I — DESIGN & SPECIFICATION

The complete design set. These documents were written **before** implementation, on the
explicit instruction that architecture and MVP specification be completed and approved
prior to any code being written.
""")
    for d in docs:
        w(f"\n\n---\n\n<a id=\"{slug(d.name)}\"></a>\n")
        w(f"> **Source file:** `docs/{d.name}` · {len(read(d).splitlines()):,} lines\n")
        w(read(d).rstrip())

    # ---------------------------------------------------------------- Part II
    w(f"""

---
---

# PART II — SOURCE CODE

{len(code)} files, {code_lines:,} lines, reproduced in full from `{branch}` @ `{commit}`.

| Group | Files | Lines |
|---|---|---|""")
    for label, _ in CODE_GROUPS:
        group = [p for l, p in code if l == label]
        if not group:
            continue
        gl = sum(len(read(ROOT / p).splitlines()) for p in group)
        w(f"| {label} | {len(group)} | {gl:,} |")
    w(f"| **Total** | **{len(code)}** | **{code_lines:,}** |\n")

    current = None
    for label, rel in code:
        if label != current:
            current = label
            w(f"\n---\n\n# {label}\n")
        text = read(ROOT / rel)
        lines = len(text.splitlines())
        w(f"\n\n<a id=\"{slug(str(rel))}\"></a>\n")
        w(f"### `{rel}`\n")
        w(f"*{lines:,} lines*\n")
        w(f"```{fence_for(ROOT / rel)}")
        w(text.rstrip("\n"))
        w("```\n")

    # ---------------------------------------------------------------- colophon
    w(f"""
---
---

# COLOPHON

Generated {now.strftime('%Y-%m-%d %H:%M UTC')} from `{branch}` @ `{commit}` by
`scripts/build_complete_package.py`. Re-running that script regenerates this document from
the current tree.

**Licensing posture recorded in this package** (see Part I, documents 14–16):

- No external broker adapter ships. Alpaca's terms permit only personal, non-commercial
  use; CIRO Dealer Member Rule 3200 bars Canadian symbols from external
  order-execution-only dealers.
- FINRA short-sale volume metrics are gated behind a research-only entitlement tier.
- Cboe VIX index levels are licensed commercial data; the volatility work derives its
  inputs internally and references no third-party index value.
- Canadian symbols never route to an external adapter. This is a regulatory constraint,
  not a configuration preference.
""")

    out_path.write_text("\n".join(parts), encoding="utf-8")
    size_mb = out_path.stat().st_size / (1024 * 1024)
    total = len("\n".join(parts).splitlines())
    print(f"Wrote {out_path}")
    print(f"  {total:,} lines, {size_mb:.2f} MB")
    print(f"  {len(docs)} design documents, {len(code)} source files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
