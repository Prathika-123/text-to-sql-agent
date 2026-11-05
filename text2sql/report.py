"""Build a PDF report of a whole chat session (questions, answers, SQL, result tables, charts).

    pdf_bytes = build_report_pdf(messages, data_source="retail_inventory.csv", provider="gemini")

`messages` is the Streamlit session list: {"role": "user", "content": str} and
{"role": "assistant", "result": QueryResult}.  The module has no Streamlit dependency, so it is unit-testable
and usable from the CLI too.
"""
from __future__ import annotations

import io
import textwrap
from datetime import datetime
from numbers import Number
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, KeepTogether, Paragraph, Preformatted, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

MAX_TABLE_ROWS = 50      # rows printed per result (the full result is still available as CSV in the UI)
MAX_CELL_CHARS = 60
SQL_WRAP = 88
SERIES_COLORS = [colors.HexColor(c) for c in ("#2563eb", "#f97316", "#16a34a", "#9333ea", "#dc2626", "#0891b2")]
INK, MUTED, RULE, PANEL = (colors.HexColor(c) for c in ("#111827", "#6b7280", "#d1d5db", "#f3f4f6"))

# The built-in PDF fonts only cover Latin-1; map common typography to safe equivalents and drop the rest.
_REPLACEMENTS = {"→": "->", "←": "<-", "‘": "'", "’": "'", "“": '"', "”": '"',
                 "–": "-", "—": "-", "…": "...", "•": "*", " ": " ", "₹": "Rs ",
                 "≤": "<=", "≥": ">=", "≠": "!="}


def _safe(text) -> str:
    s = "" if text is None else str(text)
    for k, v in _REPLACEMENTS.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1").replace("\r", "")


def _p(text) -> str:
    """Escape for ReportLab's mini-markup; keep line breaks."""
    return escape(_safe(text)).replace("\n", "<br/>")


def _fmt(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:,.2f}" if abs(v) < 1e15 else str(v)
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}" if abs(v) >= 10_000 else str(v)   # keep ids / years readable, group big numbers
    s = _safe(v)
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS - 3] + "..."


def _axis_fmt(v: float) -> str:
    a = abs(v)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{v / div:.4g}{suffix}"
    return f"{v:.4g}"


def _is_num(v) -> bool:
    return isinstance(v, Number) and not isinstance(v, bool)


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=10, leading=14, textColor=INK)
    return {
        "title": ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=22, leading=26, spaceAfter=4),
        "meta": ParagraphStyle("meta", parent=body, fontSize=9, leading=13, textColor=MUTED),
        "q": ParagraphStyle("q", parent=body, fontName="Helvetica-Bold", fontSize=12, leading=16, spaceBefore=6, spaceAfter=4),
        "qnum": ParagraphStyle("qnum", parent=body, fontSize=8, leading=10, textColor=MUTED),
        "body": body,
        "note": ParagraphStyle("note", parent=body, fontSize=9, leading=12, textColor=MUTED, spaceBefore=2),
        "label": ParagraphStyle("label", parent=body, fontName="Helvetica-Bold", fontSize=8, leading=10,
                                textColor=MUTED, spaceBefore=8, spaceAfter=2, keepWithNext=1),
        "error": ParagraphStyle("error", parent=body, textColor=colors.HexColor("#b91c1c")),
        "th": ParagraphStyle("th", parent=body, fontName="Helvetica-Bold", fontSize=8, leading=10),
        "td": ParagraphStyle("td", parent=body, fontSize=8, leading=10),
        "tdr": ParagraphStyle("tdr", parent=body, fontSize=8, leading=10, alignment=TA_RIGHT),
        "thr": ParagraphStyle("thr", parent=body, fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_RIGHT),
        "code": ParagraphStyle("code", parent=body, fontName="Courier", fontSize=8, leading=10),
    }


def _sql_block(sql: str, width: float, st) -> Table:
    lines: list[str] = []
    for line in _safe(sql).strip().splitlines():
        lines += textwrap.wrap(line, SQL_WRAP, subsequent_indent="    ", break_long_words=True,
                               drop_whitespace=False) or [""]
    t = Table([[Preformatted("\n".join(lines), st["code"])]], colWidths=[width])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PANEL), ("BOX", (0, 0), (-1, -1), 0.5, RULE),
                           ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


def _result_table(columns, rows, width, st) -> Table:
    shown = rows[:MAX_TABLE_ROWS]
    numeric = [all(_is_num(r[i]) or r[i] is None for r in shown) and any(_is_num(r[i]) for r in shown)
               for i in range(len(columns))]
    head = [Paragraph(_p(c), st["thr" if numeric[i] else "th"]) for i, c in enumerate(columns)]
    data = [head] + [[Paragraph(escape(_fmt(v)), st["tdr" if numeric[i] else "td"]) for i, v in enumerate(r)]
                     for r in shown]
    # Column width ∝ longest content (bounded) so narrow id columns don't get as much room as long text ones
    weights = [min(max(len(_safe(c)), *(len(_fmt(r[i])) for r in shown)), 40) + 4 for i, c in enumerate(columns)]
    total = sum(weights)
    t = Table(data, colWidths=[width * w / total for w in weights], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PANEL), ("LINEBELOW", (0, 0), (-1, 0), 0.8, MUTED),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def _chart(res, width) -> Drawing | None:
    """Re-draw the chart the UI shows (res.chart = {"kind", "x", "y": [cols]}) as a vector graphic."""
    spec = res.chart
    if not spec or not res.rows or spec["x"] not in res.columns:
        return None
    try:
        xi = res.columns.index(spec["x"])
        ys = [c for c in spec["y"] if c in res.columns][:4]
        if not ys:
            return None
        rows = res.rows[:30]
        cats = [_safe(r[xi])[:14] for r in rows]
        series = []
        for c in ys:
            i = res.columns.index(c)
            series.append([float(r[i]) if _is_num(r[i]) else None for r in rows])
        kind = spec.get("kind", "bar")
        legend_h = 16 if len(ys) > 1 else 0
        d = Drawing(width, 190 + legend_h)
        ch = HorizontalLineChart() if kind == "line" else VerticalBarChart()
        ch.x, ch.y, ch.width, ch.height = 45, 38, width - 60, 190 - 38 - 8
        ch.data = series if kind == "line" else [[v or 0 for v in s] for s in series]
        ch.categoryAxis.categoryNames = cats
        ch.categoryAxis.labels.fontSize = 7
        ch.categoryAxis.labels.boxAnchor = "ne" if len(cats) > 6 else "n"
        ch.categoryAxis.labels.angle = 35 if len(cats) > 6 else 0
        ch.valueAxis.labels.fontSize = 7
        ch.valueAxis.labelTextFormat = _axis_fmt
        ch.categoryAxis.labels.fontName = ch.valueAxis.labels.fontName = "Helvetica"
        ch.valueAxis.visibleGrid, ch.valueAxis.gridStrokeColor = True, RULE
        vals = [v for s in series for v in s if v is not None]
        if kind == "bar" and vals and min(vals) >= 0:
            ch.valueAxis.valueMin = 0
        if kind == "bar":
            ch.groupSpacing, ch.barSpacing = 6, 1
            for k in range(len(ys)):
                ch.bars[k].fillColor = SERIES_COLORS[k]
                ch.bars[k].strokeColor = None
        else:
            for k in range(len(ys)):
                ch.lines[k].strokeColor, ch.lines[k].strokeWidth = SERIES_COLORS[k], 1.5
        d.add(ch)
        if legend_h:
            lg = Legend()
            lg.x, lg.y, lg.fontSize, lg.fontName, lg.alignment = 45, 190 + legend_h - 4, 8, "Helvetica", "right"
            lg.deltax, lg.columnMaximum, lg.boxAnchor = 90, 1, "nw"
            lg.colorNamePairs = [(SERIES_COLORS[k], _safe(c)) for k, c in enumerate(ys)]
            d.add(lg)
        return d
    except Exception:       # a chart is a nicety; never fail the whole report because of it
        return None


def _pair_turns(messages: list[dict]) -> list[tuple[str, object | None]]:
    """[(question, QueryResult | None)] — tolerant of a trailing unanswered question."""
    turns: list[tuple[str, object | None]] = []
    for m in messages:
        if m.get("role") == "user":
            turns.append((m.get("content", ""), None))
        elif m.get("role") == "assistant" and turns and turns[-1][1] is None:
            turns[-1] = (turns[-1][0], m.get("result"))
    return turns


def _turn_flowables(n: int, question: str, res, width: float, st) -> list:
    head = [Paragraph(f"QUESTION {n}", st["qnum"]), Paragraph(_p(question), st["q"])]
    out: list = []
    if res is None:
        return [KeepTogether(head), Paragraph("No answer was recorded for this question.", st["note"])]
    if res.clarification:
        out += [Paragraph("ASSISTANT NEEDS CLARIFICATION", st["label"]), Paragraph(_p(res.clarification), st["body"])]
    if res.answer:
        out += [Paragraph("ANSWER", st["label"]), Paragraph(_p(res.answer), st["body"])]
    if res.assumptions:
        out.append(Paragraph(f"<b>Assumption:</b> {_p(res.assumptions)}", st["note"]))
    if res.error:
        out += [Paragraph("ERROR", st["label"]), Paragraph(_p(res.error), st["error"])]
    if res.sql:
        out += [Paragraph("GENERATED SQL", st["label"]), _sql_block(res.sql, width, st)]
        retries = [a for a in res.attempts if a.error]
        if retries:
            out.append(Paragraph(f"{len(retries)} earlier attempt(s) failed and were auto-corrected.", st["note"]))
    if res.rows:
        shown = min(len(res.rows), MAX_TABLE_ROWS)
        out += [Paragraph("RESULT", st["label"]), _result_table(res.columns, res.rows, width, st)]
        notes = []
        if len(res.rows) > shown:
            notes.append(f"Showing first {shown} of {len(res.rows)} rows")
        elif res.truncated:
            notes.append(f"Showing first {len(res.rows)} rows (result was capped)")
        if notes:
            out.append(Paragraph(notes[0] + ".", st["note"]))
        chart = _chart(res, width)
        if chart:
            out += [Paragraph("CHART", st["label"]), chart]
    # Keep the heading attached to whatever follows so a question never sits alone at the bottom of a page
    first = out.pop(0) if out else Spacer(1, 1)
    return [KeepTogether(head + [first])] + out


def build_report_pdf(messages: list[dict], *, data_source: str = "", provider: str = "", model: str = "",
                     generated_at: datetime | None = None) -> bytes:
    """Render the conversation to PDF and return the file's bytes."""
    st = _styles()
    when = (generated_at or datetime.now()).strftime("%d %b %Y, %H:%M")
    turns = _pair_turns(messages)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="Text-to-SQL conversation report",
                            author="Text-to-SQL", subject=_safe(data_source))
    width = doc.width

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(d.leftMargin, 10 * mm, "Text-to-SQL conversation report")
        canvas.drawRightString(A4[0] - d.rightMargin, 10 * mm, f"Page {d.page}")
        canvas.restoreState()

    answered = sum(1 for _, r in turns if r is not None and getattr(r, "ok", False))
    meta = [f"Generated {when}"]
    if data_source:
        meta.append(f"Data source: {_p(data_source)}")
    if provider:
        meta.append(f"LLM: {_p(provider)}" + (f" ({_p(model)})" if model else ""))
    story: list = [Paragraph("Conversation report", st["title"]),
                   Paragraph(" &nbsp;|&nbsp; ".join(meta), st["meta"]),
                   Paragraph(f"{len(turns)} question(s), {answered} answered successfully. "
                             "All figures were computed by running SQL on the data source above.", st["meta"]),
                   Spacer(1, 6), HRFlowable(width="100%", thickness=0.8, color=RULE), Spacer(1, 6)]
    if not turns:
        story.append(Paragraph("This conversation has no questions yet.", st["body"]))
    for n, (q, res) in enumerate(turns, 1):
        story += _turn_flowables(n, q, res, width, st)
        story += [Spacer(1, 8), HRFlowable(width="100%", thickness=0.4, color=RULE), Spacer(1, 4)]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
