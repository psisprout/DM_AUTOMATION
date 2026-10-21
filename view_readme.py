#!/usr/bin/env python3
"""README.md 를 보기 좋은 HTML 로 만들어 Firefox 로 연다.

마크다운 파서(vendor/marked.min.js)와 README 내용을 HTML 한 파일에 모두
넣기 때문에 인터넷 연결이나 추가 패키지 설치 없이 동작한다.

사용법:
    python3 view_readme.py              # README.md
    python3 view_readme.py docs/foo.md  # 다른 마크다운 파일
"""
import base64
import html
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE = BASE_DIR / "readme_viewer.html"
MARKED_JS = BASE_DIR / "vendor" / "marked.min.js"

EMBED_TAG = '<script id="embedded" type="text/plain"></script>'
MARKED_TAG = '<script src="vendor/marked.min.js"></script>'


def build_html(md_path: Path) -> str:
    page = TEMPLATE.read_text(encoding="utf-8")
    b64 = base64.b64encode(md_path.read_bytes()).decode("ascii")
    name = html.escape(md_path.name, quote=True)
    page = page.replace(
        EMBED_TAG,
        f'<script id="embedded" type="text/plain" data-name="{name}">{b64}</script>',
    )
    # 임시 폴더에서 열리므로 상대경로 대신 파서 코드를 직접 넣는다
    page = page.replace(MARKED_TAG, f"<script>\n{MARKED_JS.read_text(encoding='utf-8')}\n</script>")
    return page


def open_in_browser(path: Path) -> None:
    firefox = shutil.which("firefox")
    if firefox:
        subprocess.Popen([firefox, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(path.as_uri())


def main() -> int:
    md_path = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE_DIR / "README.md"
    if not md_path.is_file():
        print(f"파일을 찾을 수 없습니다: {md_path}", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile("w", suffix=".html", prefix="readme_view_",
                                     encoding="utf-8", delete=False) as f:
        f.write(build_html(md_path.resolve()))
        out = Path(f.name)

    print(f"열기: {out}")
    open_in_browser(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
