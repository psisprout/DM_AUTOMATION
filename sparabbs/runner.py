"""Run the SPICE deck and locate the Touchstone file it produced."""

from __future__ import annotations

import glob
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

#: the user's launcher; {deck}, {cpu} and {dir} are substituted
DEFAULT_COMMAND = "primesim_sub -spice -cpu {cpu} -i {deck}"

_WINDOWS = os.name == "nt"


def split_command(text: str) -> list[str]:
    """Split a command line into argv the way the host platform expects.

    POSIX splitting treats a backslash as an escape, which silently destroys
    every Windows path in the template (``C:\\work\\d.sp`` -> ``C:workd.sp``),
    so on Windows the backslashes are kept and surrounding quotes stripped by
    hand instead.
    """
    if not _WINDOWS:
        return shlex.split(text)
    out = []
    for tok in shlex.split(text, posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out



class RunnerError(RuntimeError):
    """Raised when the simulator cannot be launched."""


@dataclass
class RunSpec:
    deck_path: str
    command_template: str = DEFAULT_COMMAND
    cpu: int = 4
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    timeout: float | None = None

    def work_dir(self) -> str:
        return self.cwd or os.path.dirname(os.path.abspath(self.deck_path))

    def argv(self) -> list[str]:
        text = self.command_template.format(
            deck=os.path.basename(self.deck_path),
            deck_abs=os.path.abspath(self.deck_path),
            cpu=self.cpu,
            dir=self.work_dir(),
        )
        argv = split_command(text)
        if not argv:
            raise RunnerError("the run command is empty")
        return argv

    def display(self) -> str:
        return subprocess.list2cmdline(self.argv()) if _WINDOWS else " ".join(
            shlex.quote(a) for a in self.argv()
        )


@dataclass
class RunResult:
    argv: list[str]
    returncode: int
    log: str
    seconds: float
    output_snp: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(spec: RunSpec, on_line: Callable[[str], None] | None = None) -> RunResult:
    """Run the deck, streaming each output line to ``on_line``.

    Used by the CLI; the GUI drives the same command through ``QProcess`` so the
    window stays responsive.
    """
    argv = spec.argv()
    env = dict(os.environ)
    env.update(spec.env)
    started = time.time()
    lines: list[str] = []
    try:
        proc = subprocess.Popen(
            argv,
            cwd=spec.work_dir(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise RunnerError(
            f"cannot run {argv[0]!r}: {exc}. Check the command template and PATH."
        ) from exc

    with proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            if on_line:
                on_line(line)
        proc.wait(timeout=spec.timeout)
    return RunResult(
        argv=argv,
        returncode=proc.returncode,
        log="\n".join(lines),
        seconds=time.time() - started,
    )


def find_output_snp(
    out_dir: str,
    nports: int,
    preferred: str = "",
    newer_than: float = 0.0,
) -> str:
    """Find the ``.sNp`` the run produced.

    Prefers the name the deck asked for, then the newest matching file written
    after ``newer_than``.  Returns "" when nothing is found.
    """
    if preferred and _fresh(preferred, newer_than):
        return os.path.abspath(preferred)
    matches = [
        p
        for p in glob.glob(os.path.join(out_dir, f"*.s{nports}p"))
        + glob.glob(os.path.join(out_dir, f"*.S{nports}P"))
        if _fresh(p, newer_than)
    ]
    if not matches:
        return ""
    return os.path.abspath(max(matches, key=os.path.getmtime))


def _fresh(path: str, newer_than: float) -> bool:
    return os.path.isfile(path) and os.path.getmtime(path) >= newer_than - 1.0


def scan_log_for_errors(log: str | Iterable[str]) -> list[str]:
    """Pull the lines a SPICE run uses to report trouble."""
    text = log if isinstance(log, str) else "\n".join(log)
    hits = []
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ("**error", "error:", "fatal", "cannot open", "no such")):
            hits.append(line.strip())
    return hits[:50]
