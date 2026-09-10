#!/bin/sh
# Run the suite against the Python the site actually uses.
#
# tests/test_compat.py catches version-gated APIs statically, but only running
# on the real interpreter proves it: `tuple[int, int]` parses everywhere and
# fails when evaluated, which no syntax check can see.
#
#   sh tests/run_on_py38.sh          # uses .venv38 if present, else builds it
set -e
here=$(cd "$(dirname "$0")/.." && pwd)
venv=${SPARABBS_PY38_VENV:-$here/.venv38}

if [ ! -x "$venv/bin/python" ]; then
    command -v uv >/dev/null || { echo "install uv first: pip install uv" >&2; exit 1; }
    uv python install 3.8
    uv venv --python 3.8 "$venv"
    uv pip install --python "$venv/bin/python" numpy PyQt5 matplotlib
fi

"$venv/bin/python" -c 'import sys, numpy; print("python", sys.version.split()[0], "| numpy", numpy.__version__)'
cd "$here"
"$venv/bin/python" tests/make_fixtures.py >/dev/null
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg "$venv/bin/python" -m unittest discover -s tests -t .
