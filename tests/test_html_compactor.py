from app.html_compactor import (
    fallback_html_to_markdown,
    html_to_markdown_is_equivalent,
)


def test_fallback_html_to_markdown_keeps_structure_and_drops_page_noise():
    html = """<!doctype html>
<html>
<head>
  <title>Prompt Compression Guide</title>
  <style>.ad { display: block; }</style>
  <script src="/tracking.js"></script>
</head>
<body>
  <nav><a href="/">Home</a><a href="/pricing">Pricing</a></nav>
  <aside class="ad">Sponsored: Buy more tokens</aside>
  <main>
    <article>
      <h1>Prompt Compression Guide</h1>
      <p>Reduce prompt tokens while preserving constraints.</p>
      <h2>Do not compress</h2>
      <ul>
        <li>Exact code blocks</li>
        <li>IDs, dates, URLs, and thresholds</li>
      </ul>
      <blockquote>Preserve hard constraints.</blockquote>
    </article>
  </main>
</body>
</html>"""

    markdown = fallback_html_to_markdown(html)

    assert "# Prompt Compression Guide" in markdown
    assert "Reduce prompt tokens while preserving constraints." in markdown
    assert "## Do not compress" in markdown
    assert "- Exact code blocks" in markdown
    assert "> Preserve hard constraints." in markdown
    assert ".ad" not in markdown
    assert "tracking.js" not in markdown


def test_html_preservation_signature_accepts_main_text_and_links_in_order():
    html = (
        '<html><body><main><h1>Incident</h1><p>Do not raise retry_limit above 3.</p>'
        '<a href="https://example.com/runbook">Runbook</a></main></body></html>'
    )
    markdown = (
        "# Incident\n\nDo not raise retry_limit above 3.\n\n"
        "[Runbook](https://example.com/runbook)"
    )

    assert html_to_markdown_is_equivalent(html, markdown) is True


def test_html_preservation_signature_rejects_forms_code_and_missing_links():
    form = "<html><body><main><form><input></form></main></body></html>"
    code = "<html><body><main><pre>  exact</pre></main></body></html>"
    link = '<html><body><main><a href="https://example.com">Docs</a></main></body></html>'

    assert html_to_markdown_is_equivalent(form, "") is False
    assert html_to_markdown_is_equivalent(code, "exact") is False
    assert html_to_markdown_is_equivalent(link, "Docs") is False
