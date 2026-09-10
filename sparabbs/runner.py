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
        argv = shlex.split(text)
        if not argv:
            raise RunnerError("the run command is empty")
        return argv

    def display(self) -> str:
        return " ".join(shlex.quote(a) for a in self.argv())


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
    exclude: Iterable[str] = (),
) -> str:
    """Find the ``.sNp`` the run produced.

    Prefers the name the deck asked for, then the newest matching file written
    after ``newer_than``.  ``exclude`` names files that must never be taken for
    a result - above all the reference .sNp, which often sits in the working
    directory and would otherwise be compared against itself.  Returns "" when
    nothing is found.
    """
    skip = {os.path.abspath(p) for p in exclude}
    if preferred and _fresh(preferred, newer_than):
        if os.path.abspath(preferred) not in skip:
            return os.path.abspath(preferred)
    matches = {
        os.path.abspath(p)
        for pat in (f"*.s{nports}p", f"*.S{nports}P")
        for p in glob.glob(os.path.join(out_dir, pat))
        if _fresh(p, newer_than)
    } - skip
    if not matches:
        return ""
    return max(matches, key=os.path.getmtime)


class OutputWatcher:
    """Wait for the simulator to finish writing its Touchstone file.

    The launcher is usually a queue submit command - it returns as soon as the
    job is accepted, long before the simulation runs - so process exit says
    nothing about whether results exist.  Poll for the file instead, and only
    accept it once its size has stopped changing, because a large .sNp arriving
    over NFS is visible well before it is complete.
    """

    def __init__(
        self,
        out_dir: str,
        nports: int,
        preferred: str = "",
        newer_than: float = 0.0,
        stable_polls: int = 2,
        exclude: Iterable[str] = (),
    ) -> None:
        self.out_dir = out_dir
        self.nports = nports
        self.preferred = preferred
        self.newer_than = newer_than
        self.stable_polls = stable_polls
        self.exclude = {os.path.abspath(p) for p in exclude}
        self._seen: dict[str, int] = {}
        self._candidate = ""
        self._size = -1
        self._steady = 0
        self._scan()  # remember what was already there, so only new files report
        self._initial = dict(self._seen)

    def poll(self) -> tuple[str, list[str]]:
        """Return ``(finished_path, newly_appeared_files)``.

        ``finished_path`` stays "" until a matching file has held the same size
        across ``stable_polls`` consecutive calls.
        """
        appeared = self._scan()
        path = find_output_snp(
            self.out_dir,
            self.nports,
            self.preferred,
            self.newer_than,
            exclude=self.exclude | self._untouched(),
        )
        if not path:
            self._candidate, self._size, self._steady = "", -1, 0
            return "", appeared

        try:
            size = os.path.getsize(path)
        except OSError:
            return "", appeared

        if path == self._candidate and size == self._size and size > 0:
            self._steady += 1
        else:
            self._candidate, self._size, self._steady = path, size, 0
        return (path if self._steady >= self.stable_polls else ""), appeared

    def _scan(self) -> list[str]:
        """Names that showed up, or grew, in the working directory since the last poll."""
        appeared = []
        try:
            entries = os.listdir(self.out_dir)
        except OSError:
            return appeared
        for name in sorted(entries):
            full = os.path.join(self.out_dir, name)
            try:
                if not os.path.isfile(full) or os.path.getmtime(full) < self.newer_than - 1.0:
                    continue
                size = os.path.getsize(full)
            except OSError:
                continue
            if name not in self._seen:
                appeared.append(name)
            self._seen[name] = size
        return appeared

    def _untouched(self) -> set[str]:
        """Files that were already here and have not changed - never results."""
        return {
            os.path.abspath(os.path.join(self.out_dir, name))
            for name, size in self._initial.items()
            if self._seen.get(name) == size
        }

    def produced(self) -> list[str]:
        """Files that showed up after the watch began, for a diagnostic message."""
        self._scan()
        return sorted(set(self._seen) - set(self._initial))


def wait_for_output(
    watcher: OutputWatcher,
    timeout: float = 0.0,
    poll_interval: float = 2.0,
    on_event: Callable[[str], None] | None = None,
) -> str:
    """Block until the watcher sees a finished file, or ``timeout`` seconds pass.

    ``timeout`` of 0 waits indefinitely.  Used by the CLI; the GUI drives the
    same watcher from a timer so the window stays responsive.
    """
    started = time.time()
    while True:
        path, appeared = watcher.poll()
        for name in appeared:
            if on_event:
                on_event(f"appeared: {name}")
        if path:
            return path
        if timeout and time.time() - started >= timeout:
            return ""
        time.sleep(poll_interval)


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
