#!/usr/bin/env python3
"""primesim-dm checks for the in-house MCP tools (run by run_script).

    dm_deck.py <command> --deck /abs/top.sp [--output /abs/out] [--json] ...

Copy this file into the MCP server's scripts/ folder.  It finds the
primesim-dm package from PRIMESIM_DM_HOME, or, if that is not set, from the
folders above this file (Path(__file__).parent, never the working
directory, which is scripts/ and not where the package is).  All the logic
is primesim_dm.tool_cli; see it for the options and the output rules.
"""

import os
import sys
from pathlib import Path


def _home():
    env = os.environ.get("PRIMESIM_DM_HOME")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for folder in [here] + list(here.parents)[:3]:
        if (folder / "primesim_dm").is_dir():
            return folder
    print("[dm_deck] primesim-dm not found: set PRIMESIM_DM_HOME to the "
          "primesim-dm folder", file=sys.stderr)
    print('{"status": "error", "message": "primesim-dm 을 찾지 못했습니다. '
          'PRIMESIM_DM_HOME 을 primesim-dm 폴더로 설정하세요."}')
    sys.exit(2)


sys.path.insert(0, str(_home()))

from primesim_dm.tool_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
