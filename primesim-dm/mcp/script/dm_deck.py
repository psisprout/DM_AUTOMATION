#!/usr/bin/env python3
"""primesim-dm checks for the in-house MCP tools (run by run_script).

    dm_deck.py <command> --deck /abs/top.sp [--output /abs/out] [--json] ...

Copy this file into the MCP server's script/ folder.  It finds the
primesim-dm package from PRIMESIM_DM_HOME, or, if that is not set, from the
folder this file sits in and the ones above it (so it also runs in place,
from primesim-dm/mcp/script/).  All the logic is primesim_dm.tool_cli.
"""

import os
import sys


def _home():
    env = os.environ.get("PRIMESIM_DM_HOME")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        if os.path.isdir(os.path.join(here, "primesim_dm")):
            return here
        here = os.path.dirname(here)
    sys.stdout.write('{"status": "error", "message": "primesim-dm not found: '
                     'set PRIMESIM_DM_HOME to the primesim-dm folder"}\n')
    sys.exit(1)


sys.path.insert(0, _home())

from primesim_dm.tool_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
