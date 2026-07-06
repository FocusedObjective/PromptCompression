from app.html_compactor import fallback_html_to_markdown


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
