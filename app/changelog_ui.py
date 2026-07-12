from html import escape
from pathlib import Path
import re

from app.version import DEPLOYMENT_VERSION


CHANGELOG_PATH = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def _inline_markdown(text: str) -> str:
    escaped = escape(text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)


def _render_changelog(markdown: str) -> str:
    parts: list[str] = []
    paragraphs: list[str] = []
    list_items: list[str] = []

    def flush_paragraph() -> None:
        if paragraphs:
            parts.append(f"<p>{_inline_markdown(' '.join(paragraphs))}</p>")
            paragraphs.clear()

    def close_list() -> None:
        if list_items:
            items = "".join(
                f"<li>{_inline_markdown(item)}</li>" for item in list_items
            )
            parts.append(f"<ul>{items}</ul>")
            list_items.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            close_list()
            continue
        if line == "# Changelog":
            continue
        if line.startswith("## "):
            flush_paragraph()
            close_list()
            parts.append(f"<h2>{_inline_markdown(line[3:])}</h2>")
            continue
        if line.startswith("### "):
            flush_paragraph()
            close_list()
            parts.append(f"<h3>{_inline_markdown(line[4:])}</h3>")
            continue
        if line.startswith("- "):
            flush_paragraph()
            list_items.append(line[2:])
            continue
        if list_items and raw_line[:1].isspace():
            list_items[-1] = f"{list_items[-1]} {line}"
            continue
        close_list()
        paragraphs.append(line)

    flush_paragraph()
    close_list()
    return "\n".join(parts)


def _load_changelog() -> str:
    try:
        return CHANGELOG_PATH.read_text(encoding="utf-8")
    except OSError:
        return "# Changelog\n\nThe changelog is unavailable in this deployment."


CHANGELOG_CONTENT = _render_changelog(_load_changelog())

CHANGELOG_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prompt Compression Changelog</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --panel: #ffffff;
      --text: #182230;
      --muted: #667085;
      --accent: #2563a6;
      --border: #d8e0ea;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.58;
    }}
    main {{ max-width: 920px; margin: 0 auto; padding: 36px 22px 72px; }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-start;
      margin-bottom: 24px;
    }}
    h1 {{ margin: 0 0 6px; font-size: clamp(2rem, 5vw, 3.2rem); line-height: 1.05; }}
    .subhead {{ margin: 0; color: var(--muted); }}
    .nav-links {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }}
    .nav-link {{
      border: 1px solid var(--border);
      border-radius: 999px;
      color: var(--accent);
      background: var(--panel);
      padding: 7px 11px;
      font-size: .88rem;
      font-weight: 700;
      text-decoration: none;
    }}
    .nav-link:hover, .nav-link:focus-visible {{ border-color: var(--accent); outline: none; }}
    article {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: 0 14px 34px rgba(31, 48, 76, .08);
      padding: clamp(24px, 5vw, 48px);
    }}
    article > p:first-child {{ color: var(--muted); font-size: 1.05rem; }}
    h2 {{
      margin: 42px 0 12px;
      padding-top: 22px;
      border-top: 2px solid var(--border);
      color: var(--accent);
      font-size: 1.7rem;
    }}
    article h2:first-of-type {{ margin-top: 26px; }}
    h3 {{ margin: 20px 0 8px; font-size: 1.05rem; letter-spacing: .04em; text-transform: uppercase; }}
    p {{ margin: 0 0 14px; }}
    ul {{ margin: 0 0 18px; padding-left: 24px; }}
    li {{ margin: 7px 0; }}
    code {{ border-radius: 5px; background: #edf2f7; padding: 2px 5px; font-size: .92em; }}
    @media (max-width: 720px) {{
      header {{ flex-direction: column; }}
      .nav-links {{ justify-content: flex-start; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Changelog</h1>
        <p class="subhead">A dated history of product, quality, and deployment improvements. Build {DEPLOYMENT_VERSION}.</p>
      </div>
      <nav class="nav-links" aria-label="Primary navigation">
        <a class="nav-link" href="/">Compression UI</a>
        <a class="nav-link" href="/eval">Eval Suite</a>
        <a class="nav-link" href="/benchmark">Benchmark</a>
        <a class="nav-link" href="/research">Research</a>
        <a class="nav-link" href="/docs">API Docs</a>
      </nav>
    </header>
    <article>
      {CHANGELOG_CONTENT}
    </article>
  </main>
</body>
</html>
"""
