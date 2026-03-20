#!/usr/bin/env python3
"""Converte UC1_SP2_Sistema_Digestorio.md para PDF com ReportLab."""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
import re

MD_FILE = "/home/anderson/.openclaw/workspace/aulas/UC1_SP2_Sistema_Digestorio.md"
PDF_FILE = "/home/anderson/.openclaw/workspace/aulas/UC1_SP2_Sistema_Digestorio.pdf"

# ── Styles ──────────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

h1 = ParagraphStyle("H1", parent=styles["Heading1"],
    fontSize=16, spaceAfter=8, spaceBefore=14,
    textColor=colors.HexColor("#1a3a5c"), leading=20)

h2 = ParagraphStyle("H2", parent=styles["Heading2"],
    fontSize=13, spaceAfter=6, spaceBefore=12,
    textColor=colors.HexColor("#1a3a5c"),
    borderPad=4, leading=16)

h3 = ParagraphStyle("H3", parent=styles["Heading3"],
    fontSize=11, spaceAfter=4, spaceBefore=8,
    textColor=colors.HexColor("#2e6da4"), leading=14)

body = ParagraphStyle("Body", parent=styles["Normal"],
    fontSize=9.5, spaceAfter=4, spaceBefore=2,
    leading=13, alignment=TA_JUSTIFY)

bullet = ParagraphStyle("Bullet", parent=styles["Normal"],
    fontSize=9.5, spaceAfter=2, spaceBefore=1,
    leading=13, leftIndent=14, bulletIndent=4)

bullet2 = ParagraphStyle("Bullet2", parent=styles["Normal"],
    fontSize=9.5, spaceAfter=2, spaceBefore=1,
    leading=13, leftIndent=28, bulletIndent=18)

code_style = ParagraphStyle("Code", parent=styles["Normal"],
    fontSize=8.5, fontName="Courier", leading=12,
    backColor=colors.HexColor("#f4f4f4"),
    leftIndent=10, rightIndent=10, spaceAfter=6, spaceBefore=6)

def escape(text):
    """Escapa caracteres especiais do ReportLab mas preserva negrito/itálico."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # restaura bold/italic markup
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'`(.+?)`', r'<font name="Courier" size="9">\1</font>', text)
    # sup H⁺ etc
    text = text.replace("⁺", "<super>+</super>").replace("⁻", "<super>-</super>")
    text = text.replace("₁", "<sub>1</sub>").replace("₂", "<sub>2</sub>")
    text = text.replace("₃", "<sub>3</sub>").replace("₄", "<sub>4</sub>")
    return text

def parse_md(path):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    story = []
    i = 0
    in_table = False
    table_rows = []
    in_code = False
    code_lines = []

    while i < len(lines):
        line = lines[i].rstrip("\n")

        # Code block
        if line.strip().startswith("```"):
            if not in_code:
                in_code = True
                code_lines = []
            else:
                in_code = False
                code_text = "\n".join(code_lines)
                story.append(Paragraph(escape(code_text).replace("\n", "<br/>"), code_style))
            i += 1
            continue

        if in_code:
            code_lines.append(line)
            i += 1
            continue

        # Table detection
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                in_table = True
                table_rows = []
            # skip separator rows
            if re.match(r"^\s*\|[-| :]+\|\s*$", line):
                i += 1
                continue
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            table_rows.append(cols)
            i += 1
            continue
        else:
            if in_table and table_rows:
                story.append(build_table(table_rows))
                story.append(Spacer(1, 6))
                table_rows = []
                in_table = False

        stripped = line.strip()

        # H1
        if stripped.startswith("# ") and not stripped.startswith("## "):
            story.append(Spacer(1, 4))
            story.append(Paragraph(escape(stripped[2:]), h1))
            story.append(HRFlowable(width="100%", thickness=1.5,
                                     color=colors.HexColor("#1a3a5c"), spaceAfter=6))
            i += 1; continue

        # H2
        if stripped.startswith("## "):
            story.append(Spacer(1, 4))
            story.append(Paragraph(escape(stripped[3:]), h2))
            story.append(HRFlowable(width="100%", thickness=0.5,
                                     color=colors.HexColor("#aaaaaa"), spaceAfter=4))
            i += 1; continue

        # H3
        if stripped.startswith("### "):
            story.append(Paragraph(escape(stripped[4:]), h3))
            i += 1; continue

        # H4
        if stripped.startswith("#### "):
            story.append(Paragraph(f"<b>{escape(stripped[5:])}</b>", body))
            i += 1; continue

        # HR
        if stripped.startswith("---"):
            story.append(HRFlowable(width="100%", thickness=0.5,
                                     color=colors.HexColor("#cccccc"), spaceAfter=4, spaceBefore=4))
            i += 1; continue

        # Blockquote
        if stripped.startswith("> "):
            bq_style = ParagraphStyle("BQ", parent=body,
                leftIndent=16, borderLeftPadding=6,
                textColor=colors.HexColor("#555555"),
                fontName="Times-Italic")
            story.append(Paragraph(escape(stripped[2:]), bq_style))
            i += 1; continue

        # Bullet level 2
        if re.match(r"^   - |^    - |^      - ", line):
            txt = re.sub(r"^\s+- ", "", stripped)
            story.append(Paragraph(f"• {escape(txt)}", bullet2))
            i += 1; continue

        # Bullet level 1
        if stripped.startswith("- ") or stripped.startswith("* "):
            txt = stripped[2:]
            story.append(Paragraph(f"• {escape(txt)}", bullet))
            i += 1; continue

        # Numbered list
        if re.match(r"^\d+\. ", stripped):
            txt = re.sub(r"^\d+\. ", "", stripped)
            story.append(Paragraph(f"• {escape(txt)}", bullet))
            i += 1; continue

        # Empty line
        if stripped == "":
            story.append(Spacer(1, 4))
            i += 1; continue

        # Normal paragraph
        story.append(Paragraph(escape(stripped), body))
        i += 1

    # flush table
    if in_table and table_rows:
        story.append(build_table(table_rows))

    return story


def build_table(rows):
    if not rows:
        return Spacer(1, 4)

    max_cols = max(len(r) for r in rows)
    # pad rows
    padded = [r + [""] * (max_cols - len(r)) for r in rows]

    cell_style = ParagraphStyle("TC", parent=styles["Normal"],
        fontSize=8.5, leading=11)
    header_style = ParagraphStyle("TH", parent=styles["Normal"],
        fontSize=8.5, leading=11, fontName="Helvetica-Bold")

    data = []
    for ri, row in enumerate(padded):
        s = header_style if ri == 0 else cell_style
        data.append([Paragraph(escape(str(c)), s) for c in row])

    col_width = (A4[0] - 4*cm) / max_cols

    t = Table(data, colWidths=[col_width]*max_cols, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a3a5c")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#f0f4f8"), colors.white]),
        ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN",     (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",(0,0), (-1,-1), 4),
        ("RIGHTPADDING",(0,0),(-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
    ]))
    return t


def build_pdf():
    doc = SimpleDocTemplate(
        PDF_FILE,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title="UC1 SP2 - Sistema Digestório",
        author="Dr. Anderson de Freitas",
    )
    story = parse_md(MD_FILE)
    doc.build(story)
    print(f"PDF gerado: {PDF_FILE}")


if __name__ == "__main__":
    build_pdf()
