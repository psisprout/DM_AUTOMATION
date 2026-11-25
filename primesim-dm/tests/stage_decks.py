"""Small made-up decks in the shapes the stage analysis has to handle.

Not real interfaces, just their topology: a chain of big blocks with many
lanes through them, split, merged or branching.
"""


def _chain(names, sigs):
    """Hub lines passing each signal through: in-port, out-port per signal."""
    lines = []
    for i, name in enumerate(names):
        ports = []
        for s in sigs:
            ports += ["%s_s%d" % (s, i), "%s_s%d" % (s, i + 1)]
        lines.append("%s %s blk_%s" % (name, " ".join(ports), name.lower()))
    return lines


def _ends(sigs, first, last, sources=True):
    lines = []
    for s in sigs:
        if sources:
            lines.append("V_%s in_%s 0 pulse" % (s, s))
            lines.append("XTX_%s in_%s %s_s%d vdd 0 io_tx" % (s, s, s, first))
        else:
            lines.append("XTX_%s in_%s %s_s%d vdd 0 io_tx" % (s, s, s, first))
        lines.append("XRX_%s %s_s%d vdd 0 io_rx" % (s, s, last))
        lines.append("C_%s %s_s%d 0 1p" % (s, s, last))
    return lines


def lpddr_split():
    """RDL-PKG-board-PKG-RDL, the board in three pieces (two bytes, CA)."""
    groups = [["dq%d" % i for i in range(8)],
              ["dq%d" % i for i in range(8, 16)],
              ["ca%d" % i for i in range(6)]]
    sigs = [s for g in groups for s in g]
    lines = _chain(["X_RDL_TX", "X_PKG_TX"], sigs)
    for k, g in enumerate(groups):
        ports = []
        for s in g:
            ports += ["%s_s2" % s, "%s_s3" % s]
        lines.append("X_BRD_G%d %s brd_g%d" % (k, " ".join(ports), k))
    for i, name in enumerate(["X_PKG_RX", "X_RDL_RX"], 3):
        ports = []
        for s in sigs:
            ports += ["%s_s%d" % (s, i), "%s_s%d" % (s, i + 1)]
        lines.append("%s %s blk" % (name, " ".join(ports)))
    return "\n".join(lines + _ends(sigs, 0, 5)) + "\n"


def lpddr_merged():
    """The whole channel as one model."""
    sigs = ["dq%d" % i for i in range(16)]
    return "\n".join(_chain(["X_CH"], sigs) + _ends(sigs, 0, 1)) + "\n"


def lpddr_odd_lane():
    """lpddr_split with one lane's receiver missing its termination cap and
    wired through a series resistor instead - the one that differs."""
    text = lpddr_split()
    text = text.replace("XRX_dq3 dq3_s5 vdd 0 io_rx",
                        "R_dq3 dq3_s5 dq3_x 10\nXRX_dq3 dq3_x vdd 0 io_rx")
    return text


def nand_1p4p():
    """Controller - package - board - NAND package fanning out to 4 dies."""
    sigs = ["io%d" % i for i in range(8)]
    lines = _chain(["X_CPKG", "X_BRD"], sigs)
    ports = ["%s_s2" % s for s in sigs]
    for k in range(4):
        ports += ["%s_d%d" % (s, k) for s in sigs]
    lines.append("X_NPKG %s nand_pkg" % " ".join(ports))
    for k in range(4):
        lines.append("X_DIE%d %s vcc 0 nand_die"
                     % (k, " ".join("%s_d%d" % (s, k) for s in sigs)))
    for s in sigs:
        lines.append("V_%s in_%s 0 pulse" % (s, s))
        lines.append("XTX_%s in_%s %s_s0 vcc 0 io_tx" % (s, s, s))
    return "\n".join(lines) + "\n"


def hbm():
    """PHY bits - interposer - HBM base die, many lanes, no split."""
    sigs = ["dq%d" % i for i in range(64)]
    lines = _chain(["X_INTERPOSER"], sigs)
    lines.append("X_BASE %s hbm_base" % " ".join("%s_s1" % s for s in sigs))
    for s in sigs:
        lines.append("V_%s in_%s 0 pulse" % (s, s))
        lines.append("XPHY_%s in_%s %s_s0 vdd 0 phy_tx" % (s, s, s))
    return "\n".join(lines) + "\n"


def no_sources():
    """lpddr_merged with no stimulus: direction from deck order."""
    return "\n".join(l for l in lpddr_merged().splitlines()
                     if not l.startswith("V_")) + "\n"


def flyby_4rank():
    """CA fly-by: package, then board segments and four rank packages in a
    row, each rank with its die hanging off it; terminated at the end."""
    sigs = ["ca%d" % i for i in range(6)]
    lines = []
    chain = ["X_PKG", "X_SEG0", "X_RANK0", "X_SEG1", "X_RANK1", "X_SEG2",
             "X_RANK2", "X_SEG3", "X_RANK3"]
    for i, name in enumerate(chain):
        ports = []
        for s in sigs:
            ports += ["%s_s%d" % (s, i), "%s_s%d" % (s, i + 1)]
        if name.startswith("X_RANK"):
            ports += ["%s_%s" % (s, name[-1]) for s in sigs]
        lines.append("%s %s blk" % (name, " ".join(ports)))
    for r in range(4):
        lines.append("X_DIE_R%d %s vdd 0 dram_die"
                     % (r, " ".join("%s_%d" % (s, r) for s in sigs)))
    for s in sigs:
        lines.append("V_%s in_%s 0 pulse" % (s, s))
        lines.append("XTX_%s in_%s %s_s0 vdd 0 io_tx" % (s, s, s))
        lines.append("R_%s %s_s%d vtt 40" % (s, s, len(chain)))
    return "\n".join(lines) + "\n"
