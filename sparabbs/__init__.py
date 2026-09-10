"""sparabbs - validate a broadband-SPICE (BBS) model against its source S-parameters.

Pipeline: read ``a.snp`` -> generate a PrimeSim deck that re-extracts
S-parameters from ``a_sp.sp`` -> run it -> compare both in the Z domain ->
emit an XML report.
"""

__version__ = "0.1.0"

from . import compare as compare_mod  # noqa: F401  (sparabbs.compare stays a module)
from .compare import Criteria, ReferenceSpec  # noqa: F401
from .deck import DeckConfig, PinAssignment, build_deck, write_deck  # noqa: F401
from .netlist import read_netlist  # noqa: F401
from .report import write_junit  # noqa: F401
from .runner import RunSpec, find_output_snp, run  # noqa: F401
from .touchstone import Network, read_touchstone, write_touchstone  # noqa: F401
