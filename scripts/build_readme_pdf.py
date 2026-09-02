"""
build_readme_pdf.py
--------------------
Converts README.md to README.pdf for the release folder. Deliberately
simple (headings/bullets/code-blocks by convention, not a full
markdown parser), using reportlab.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted
from reportlab.lib.enums import TA_LEFT


def markdown_to_pdf(md_path: Path, pdf_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="RFQH1", fontSize=18, leading=22, spaceAfter=12, spaceBefore=6, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="RFQH2", fontSize=14, leading=18, spaceAfter=8, spaceBefore=10, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="RFQBody", fontSize=10, leading=14, spaceAfter=6, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="RFQBullet", fontSize=10, leading=14, spaceAfter=4, leftIndent=16))
    styles.add(ParagraphStyle(name="RFQMono", fontName="Courier", fontSize=8, leading=10))

    story = []
    in_code_block = False
    code_lines: list[str] = []

    def flush_code():
        nonlocal code_lines
        if code_lines:
            story.append(Preformatted("\n".join(code_lines), styles["RFQMono"]))
            story.append(Spacer(1, 8))
            code_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            if in_code_block:
                flush_code()
            in_code_block = not in_code_block
            continue
        if in_code_block:
            code_lines.append(line)
            continue

        escaped = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
        escaped = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", escaped)

        if line.startswith("# "):
            story.append(Paragraph(escaped[2:], styles["RFQH1"]))
        elif line.startswith("## "):
            story.append(Paragraph(escaped[3:], styles["RFQH2"]))
        elif line.startswith("### "):
            story.append(Paragraph(f"<b>{escaped[4:]}</b>", styles["RFQBody"]))
        elif line.strip().startswith(("- ", "* ")):
            story.append(Paragraph(f"&bull;&nbsp;&nbsp;{escaped.strip()[2:]}", styles["RFQBullet"]))
        elif line.strip().startswith("|"):
            story.append(Paragraph(escaped, styles["RFQMono"]))
        elif not line.strip():
            story.append(Spacer(1, 6))
        else:
            story.append(Paragraph(escaped, styles["RFQBody"]))

    flush_code()

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )
    doc.build(story)


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("README.md")
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("README.pdf")
    markdown_to_pdf(src, dst)
    print(f"Wrote {dst}")
