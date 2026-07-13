from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent
PDF = ROOT / "rendered" / "JSON_Protection_Before_After_Integrity_Study.pdf"
OUT = ROOT / "rendered" / "pages"
OUT.mkdir(parents=True, exist_ok=True)

pdf = pdfium.PdfDocument(str(PDF))
pages = []
for index in range(len(pdf)):
    image = pdf[index].render(scale=1.8).to_pil().convert("RGB")
    image.save(OUT / f"page-{index + 1:02d}.png")
    pages.append(image)

thumb_w = 765
thumbs = []
for index, page in enumerate(pages, start=1):
    height = round(page.height * thumb_w / page.width)
    thumb = page.resize((thumb_w, height))
    canvas = Image.new("RGB", (thumb_w + 20, height + 50), "white")
    canvas.paste(thumb, (10, 35))
    ImageDraw.Draw(canvas).text((14, 10), f"Page {index}", fill="#333333")
    thumbs.append(ImageOps.expand(canvas, border=1, fill="#B8C2CC"))

for start in range(0, len(thumbs), 4):
    group = thumbs[start:start + 4]
    rows = [group[i:i + 2] for i in range(0, len(group), 2)]
    sheet = Image.new(
        "RGB",
        (max(x.width for x in group) * 2, sum(max(x.height for x in row) for row in rows)),
        "#E6EAF0",
    )
    y = 0
    for row in rows:
        row_h = max(x.height for x in row)
        for col, thumb in enumerate(row):
            sheet.paste(thumb, (col * thumb.width, y))
        y += row_h
    sheet.save(OUT / f"contact-{start + 1:02d}-{start + len(group):02d}.png")

print(f"pages={len(pages)}")
