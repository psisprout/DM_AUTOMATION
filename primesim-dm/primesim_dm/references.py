"""Reference pattern / option files a deck is expected to be using.

                        >>> THIS FILE IS THE ONE YOU EDIT <<<

Put the absolute path of each reference file in the list below.  On every
``lint`` run the deck is checked against them **by content**, not by path:
a copy sitting somewhere else under a different name still counts as a
match, which is the point - what matters is that the deck is simulating with
the pattern and options everyone agreed on, not where the file happens to
live.

If a reference has no match in the deck, lint warns:

    unproper-pattern-file    for anything in REFERENCE_PATTERN_FILES
    unproper-option-file     for anything in REFERENCE_OPTION_FILES

Both lists start empty, and an empty list is checked for nothing at all - a
site that has not filled this in gets no warnings rather than noise.  Blank
entries are ignored too, so a placeholder left behind costs nothing.
"""

# Pattern files (stimulus / vector files) every deck should be driving with.
# One absolute path per line, e.g.
#     r"/proj/dm/ref/pattern_lpddr5_wr.pat",
REFERENCE_PATTERN_FILES = [
    r"",
]

# Option / corner files every deck should be including, e.g.
#     r"/proj/dm/ref/primesim_options.inc",
REFERENCE_OPTION_FILES = [
    r"",
]
