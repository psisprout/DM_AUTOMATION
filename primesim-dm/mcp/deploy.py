#!/usr/bin/env python3
"""Install the primesim-dm MCP scripts into an in-house scripts/ folder.

    python3 mcp/deploy.py /path/to/inhouse/scripts [--overwrite]

The in-house scripts/ folder holds files only, no sub-folders, so the
primesim_dm package cannot go there as a folder.  This copies the tool
scripts (mcp/scripts/dm_*.py) and writes each package module next to them
as a flat file, primesim_dm_<module>.py, with its imports rewritten to
match: ``from . import deck as deck_mod`` becomes
``import primesim_dm_deck as deck_mod``.  The tool scripts find those files
from Path(__file__).parent.

Existing files are replaced only with --overwrite.  Prints what it wrote,
and names any older dm_*.py there that no tool uses any more (it does not
delete them).  Run it again after changing primesim-dm.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent / "primesim_dm"
PREFIX = "primesim_dm_"
SKIP = {"__init__.py", "__main__.py"}

_FROM_DOT_IMPORT = re.compile(r"^(\s*)from \. import (\w+)(?: as (\w+))?\s*$")
_FROM_DOT_MOD = re.compile(r"^(\s*)from \.(\w+) import (.+)$")


def flatten(text):
    """Rewrite a package module's relative imports for a flat folder."""
    out = []
    for line in text.splitlines(True):
        m = _FROM_DOT_IMPORT.match(line)
        if m:
            indent, mod, alias = m.groups()
            out.append("%simport %s%s as %s\n"
                       % (indent, PREFIX, mod, alias or mod))
            continue
        m = _FROM_DOT_MOD.match(line)
        if m:
            indent, mod, names = m.groups()
            out.append("%sfrom %s%s import %s\n"
                       % (indent, PREFIX, mod, names))
            continue
        if re.match(r"^\s*from \.", line):
            raise SystemExit("cannot flatten import: %s" % line.strip())
        out.append(line)
    return "".join(out)


def plan():
    """``[(target name, text)]`` for every file the scripts/ folder gets."""
    files = []
    for src in sorted((HERE / "scripts").glob("dm_*.py")):
        files.append((src.name, src.read_text(encoding="utf-8")))
    for src in sorted(PACKAGE.glob("*.py")):
        if src.name in SKIP:
            continue
        files.append((PREFIX + src.name,
                      flatten(src.read_text(encoding="utf-8"))))
    return files


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("target", help="the in-house scripts/ folder")
    p.add_argument("--overwrite", action="store_true",
                   help="replace files that are already there")
    args = p.parse_args(argv)
    target = Path(args.target).resolve()
    if not target.is_dir():
        print("no such folder: %s" % target, file=sys.stderr)
        return 2
    files = plan()
    there = [name for name, _t in files if (target / name).exists()]
    if there and not args.overwrite:
        print("already in %s (pass --overwrite to replace): %s"
              % (target, ", ".join(there)), file=sys.stderr)
        return 2
    for name, text in files:
        dst = target / name
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if name.startswith("dm_"):
            shutil.copymode(HERE / "scripts" / name, dst)
        print(dst)
    # tool scripts an earlier deploy left that no tool runs any more
    names = {name for name, _t in files}
    stale = sorted(p.name for p in target.glob("dm_*.py")
                   if p.name not in names)
    if stale:
        print("no longer used by any tool, safe to delete: %s"
              % ", ".join(stale), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
