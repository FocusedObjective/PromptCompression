from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps, ImageDraw

ROOT = Path(__file__).resolve().parent
PDF = ROOT / "rendered" / "Measuring_the_Prompt_Compression_Fidelity_Tradeoff.pdf"
OUT = ROOT / "rendered" / "pages"
OUT.mkdir(parents=True, exist_ok=True)

pdf = pdfium.PdfDocument(str(PDF))
pages = []
for index in range(len(pdf)):
    bitmap = pdf[index].render(scale=1.8)
    image = bitmap.to_pil().convert("RGB")
    path = OUT / f"page-{index + 1:02d}.png"
    image.save(path)
    pages.append(image)

for group_start in range(0, len(pages), 4):
    group = pages[group_start:group_start + 4]
    thumb_w = 765
    thumbs = []
    for idx, page in enumerate(group, start=group_start + 1):
        h = round(page.height * thumb_w / page.width)
        thumb = page.resize((thumb_w, h))
        canvas = Image.new("RGB", (thumb_w + 20, h + 55), "white")
        canvas.paste(thumb, (10, 40))
        ImageDraw.Draw(canvas).text((14, 12), f"Page {idx}", fill="#333333")
        thumbs.append(ImageOps.expand(canvas, border=1, fill="#B8C2CC"))
    sheet_w = max(x.width for x in thumbs) * 2
    row_heights = []
    for row in range(0, len(thumbs), 2):
        row_heights.append(max(x.height for x in thumbs[row:row + 2]))
    sheet = Image.new("RGB", (sheet_w, sum(row_heights)), "#E6EAF0")
    y = 0
    for row, row_h in enumerate(row_heights):
        for col, thumb in enumerate(thumbs[row * 2:row * 2 + 2]):
            sheet.paste(thumb, (col * thumb.width, y))
        y += row_h
    sheet.save(OUT / f"contact-{group_start + 1:02d}-{group_start + len(group):02d}.png")

print(f"pages={len(pages)}")
