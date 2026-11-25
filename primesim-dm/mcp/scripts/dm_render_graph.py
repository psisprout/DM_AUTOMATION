#!/usr/bin/env python3
"""The deck's connectivity as an HTML viewer, written to --output.

Run by the dm_render_graph tool in MCP.py; see --help for the options.  The work is
done by the primesim_dm_*.py files next to this file in scripts/ (put there
by mcp/deploy.py), found from Path(__file__).parent, never from the
working directory.
"""

import sys
from pathlib import Path

# no __pycache__/ folder: scripts/ holds files only
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
if (HERE / "primesim_dm_tool_cli.py").is_file():
    # deployed: the flat primesim_dm_*.py files sit next to this one
    sys.path.insert(0, str(HERE))
    from primesim_dm_tool_cli import main
else:
    # in the primesim-dm repo: mcp/scripts/ is two folders below the package
    sys.path.insert(0, str(HERE.parent.parent))
    from primesim_dm.tool_cli import main

if __name__ == "__main__":
    sys.exit(main("dm_render_graph"))
