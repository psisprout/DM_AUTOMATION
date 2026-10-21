#!/usr/bin/env bash
# README.md 를 readme_viewer.html 에 내장시켜 Firefox 로 연다.
# 사용법: ./view_readme.sh [마크다운파일]   (기본값: README.md)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MD="${1:-$DIR/README.md}"
TEMPLATE="$DIR/readme_viewer.html"

if [[ ! -f "$MD" ]]; then
  echo "파일을 찾을 수 없습니다: $MD" >&2
  exit 1
fi

OUT="$(mktemp --suffix=.html "${TMPDIR:-/tmp}/readme_view.XXXXXX")"
B64="$(base64 -w0 "$MD")"
NAME="$(basename "$MD")"

# 빈 <script id="embedded"> 태그에 base64 로 인코딩한 내용을 채워 넣는다
awk -v b64="$B64" -v name="$NAME" '
  /<script id="embedded" type="text\/plain"><\/script>/ {
    sub(/<script id="embedded" type="text\/plain"><\/script>/,
        "<script id=\"embedded\" type=\"text/plain\" data-name=\"" name "\">" b64 "</script>")
  }
  { print }
' "$TEMPLATE" > "$OUT"

if command -v firefox >/dev/null 2>&1; then
  firefox "$OUT" >/dev/null 2>&1 &
else
  echo "firefox 가 없습니다. 브라우저로 직접 여세요: $OUT"
fi
