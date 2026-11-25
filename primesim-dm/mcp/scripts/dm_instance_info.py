#!/usr/bin/env python3
"""One instance (--name): its subckt and the net on each port.

Run by the dm_instance_info tool in MCP.py; see --help for the options.  The work is
done by the primesim_dm package, which sits next to this file in scripts/
(found from Path(__file__).parent, never from the working directory).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# deployed: scripts/primesim_dm next to this file;
# in the primesim-dm repo: mcp/scripts/ two levels below the package
for _root in (HERE, HERE.parent.parent):
    if (_root / "primesim_dm").is_dir():
        sys.path.insert(0, str(_root))
        break

from primesim_dm.tool_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main("dm_instance_info"))
