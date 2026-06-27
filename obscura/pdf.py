"""Markdown-lite text to PDF bytes."""
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer


def make_pdf(title: str, body: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.5*cm, bottomMargin=2.5*cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("RMTitle", parent=styles["Heading1"], fontSize=20, spaceAfter=12)
    body_style = ParagraphStyle("RMBody", parent=styles["Normal"], fontSize=13, leading=20, spaceAfter=8)

    story = [Paragraph(title, title_style), Spacer(1, 0.4*cm)]
    for line in body.strip().splitlines():
        line = line.strip()
        if not line:
            story.append(Spacer(1, 0.3*cm))
        elif line.startswith("## "):
            story.append(Paragraph(line[3:], styles["Heading2"]))
        elif line.startswith("- "):
            story.append(Paragraph(f"• {line[2:]}", body_style))
        else:
            story.append(Paragraph(line, body_style))

    doc.build(story)
    return buf.getvalue()
