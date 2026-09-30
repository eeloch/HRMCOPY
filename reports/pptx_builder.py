"""Builds the Weekly HR Report slide deck (.pptx) from a WeeklyReportData. No file is written to disk -
callers get bytes back, to stream straight out of an API response.

Structure mirrors a narrative analyst report (headline finding, week-over-week comparison, daily
breakdown, top movers, category share, detail table, recommendations) rather than a plain stat dashboard.
"""

import io
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from .services import ComparisonRow

NAVY = RGBColor(0x1E, 0x27, 0x61)
ICE = RGBColor(0xCA, 0xDC, 0xFC)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CHARCOAL = RGBColor(0x27, 0x2D, 0x3B)
SLATE = RGBColor(0x64, 0x74, 0x8B)
LIGHT_BG = RGBColor(0xF4, 0xF6, 0xFB)
GREEN = RGBColor(0x05, 0x96, 0x69)
AMBER = RGBColor(0xD9, 0x77, 0x06)
RED = RGBColor(0xDC, 0x26, 0x26)
AMBER_TINT = RGBColor(0xFD, 0xF3, 0xE0)
AMBER_BORDER = RGBColor(0xF1, 0xC4, 0x82)

# The masthead every content slide carries - dark band, the Rotic logo, white title, a light-blue
# subtitle and page count - matching the company's own "IMPROVED HR REPORT" reference template.
HEADER_BG = RGBColor(0x0A, 0x16, 0x28)
LIGHT_BLUE = RGBColor(0x93, 0xC5, 0xFD)
SLIDE_BG = RGBColor(0xF0, 0xF4, 0xF8)
HEADER_H = Inches(0.95)
LOGO_PATH = Path(__file__).parent / "assets" / "rotic_logo.png"
TOTAL_SLIDES = 15

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def _currency(value):
    return f"₦{float(value):,.0f}"


def _blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _rect(slide, left, top, width, height, color, *, line_color=None, rounded=False, shadow=False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, left, top, width, height)
    if rounded:
        shape.adjustments[0] = 0.06
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    if line_color is not None:
        shape.line.color.rgb = line_color
        shape.line.width = Pt(0.75)
    else:
        shape.line.fill.background()
    shape.shadow.inherit = False
    if shadow:
        _soft_shadow(shape)
    return shape


def _text(slide, left, top, width, height, text, *, size=14, bold=False, color=CHARCOAL, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font="Calibri", italic=False, line_spacing=None):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    lines = text.split("\n")
    for index, line in enumerate(lines):
        paragraph = tf.paragraphs[0] if index == 0 else tf.add_paragraph()
        paragraph.alignment = align
        if line_spacing:
            paragraph.line_spacing = line_spacing
        run = paragraph.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = italic
        run.font.color.rgb = color
        run.font.name = font
    return box


def _footer(slide, label):
    _text(slide, Inches(0.6), Inches(7.08), Inches(8), Inches(0.35), label, size=10, color=SLATE)
    _text(slide, Inches(11.0), Inches(7.08), Inches(1.7), Inches(0.35), "Rotic HRM System", size=10, color=SLATE, align=PP_ALIGN.RIGHT)


def _page_background(slide, color=SLIDE_BG):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = color


def _soft_shadow(shape, *, color=RGBColor(0x1E, 0x27, 0x61), alpha=16000, blur=90000, dist=20000, direction=2700000):
    """A subtle drop shadow python-pptx has no high-level API for - matches the reference
    template's card shadows (a:outerShdw), added directly to the shape's spPr."""
    spPr = shape._element.spPr
    effect_lst = spPr.makeelement(qn("a:effectLst"), {})
    shadow = effect_lst.makeelement(qn("a:outerShdw"), {
        "blurRad": str(blur), "dist": str(dist), "dir": str(direction), "rotWithShape": "0",
    })
    color_el = shadow.makeelement(qn("a:srgbClr"), {"val": str(color)})
    alpha_el = color_el.makeelement(qn("a:alpha"), {"val": str(alpha)})
    color_el.append(alpha_el)
    shadow.append(color_el)
    effect_lst.append(shadow)
    spPr.append(effect_lst)


def _header(slide, title, subtitle, page):
    """The masthead every content slide carries: dark band, Rotic logo, title, subtitle, page count -
    matching the company's own reference report template. Also sets the slide's light body background."""
    _page_background(slide)
    _rect(slide, 0, 0, SLIDE_W, HEADER_H, HEADER_BG)
    if LOGO_PATH.exists():
        slide.shapes.add_picture(str(LOGO_PATH), Inches(0.3), Inches(0.135), height=Inches(0.68))
    _text(slide, Inches(2.8), Inches(0.16), Inches(8.6), Inches(0.4), title, size=15, bold=True, color=WHITE)
    if subtitle:
        _text(slide, Inches(2.8), Inches(0.6), Inches(8.3), Inches(0.3), subtitle, size=10, color=LIGHT_BLUE)
    _text(slide, SLIDE_W - Inches(1.5), Inches(0.63), Inches(1.2), Inches(0.28), f"{page} / {TOTAL_SLIDES}", size=9, color=LIGHT_BLUE, align=PP_ALIGN.RIGHT)


def _manual_entry_badge(slide):
    """Marks a slide whose figures HRM has no source for yet (recruitment pipeline, training
    sessions) - filled in by hand from outside the system before this deck is sent on."""
    badge = _rect(slide, Inches(10.4), Inches(1.05), Inches(2.3), Inches(0.32), AMBER_TINT, line_color=AMBER_BORDER, rounded=True)
    _text(slide, Inches(10.4), Inches(1.05), Inches(2.3), Inches(0.32), "MANUAL ENTRY REQUIRED", size=9, bold=True, color=AMBER, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    return badge


def _manual_box(slide, left, top, width, height, text):
    """A dashed-feel callout for placeholder/instructional text on a manual-entry slide."""
    _rect(slide, left, top, width, height, AMBER_TINT, line_color=AMBER_BORDER, rounded=True)
    _text(slide, left + Inches(0.2), top + Inches(0.12), width - Inches(0.4), height - Inches(0.24), text, size=11, italic=True, color=RGBColor(0x8A, 0x5A, 0x12), line_spacing=1.15)


def _stat_card(slide, left, top, width, height, value, label, color=NAVY):
    _rect(slide, left, top, width, height, WHITE, line_color=RGBColor(0xE2, 0xE6, 0xF0), rounded=True, shadow=True)
    _text(slide, left + Inches(0.15), top + Inches(0.12), width - Inches(0.3), height - Inches(0.75), str(value), size=30, bold=True, color=color)
    _text(slide, left + Inches(0.15), top + height - Inches(0.5), width - Inches(0.3), Inches(0.4), label, size=10, color=SLATE)


def _stat_row(slide, top, items, card_w=Inches(1.95), gap=Inches(0.2), start_left=Inches(0.6), card_h=Inches(1.2)):
    left = start_left
    for value, label, color in items:
        _stat_card(slide, left, top, card_w, card_h, value, label, color)
        left = left + card_w + gap


def _growth_color(pct):
    # A tiny previous-week base (e.g. attendance barely logged that week) can blow the ratio up to an
    # absurd number - that's not a real trend, so it gets a neutral color and an "N/M" label below.
    if abs(pct) > 300:
        return SLATE
    if pct > 0:
        return GREEN
    if pct < 0:
        return RED
    return SLATE


def _growth_label(pct):
    if abs(pct) > 300:
        return "N/M"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def _growth_callout(slide, left, top, width, row, subtitle=None):
    """A big growth% figure with a 'before -> after' subtext, mirroring a KPI callout on a comparison slide."""
    _text(slide, left, top, width, Inches(0.55), _growth_label(row.growth_pct), size=34, bold=True, color=_growth_color(row.growth_pct))
    _text(slide, left, top + Inches(0.58), width, Inches(0.3), row.label, size=12, bold=True, color=CHARCOAL)
    detail = subtitle or f"{_fmt(row.previous)} → {_fmt(row.current)}"
    _text(slide, left, top + Inches(0.9), width, Inches(0.3), detail, size=10, color=SLATE)


def _point_callout(slide, left, top, width, label, previous_pct, current_pct):
    """A percentage-point delta callout for a metric that is already a rate (attendance rate), where a
    relative growth% of a percentage would be misleading."""
    delta = current_pct - previous_pct
    sign = "+" if delta >= 0 else ""
    _text(slide, left, top, width, Inches(0.55), f"{sign}{delta:.1f} pts", size=34, bold=True, color=_growth_color(delta))
    _text(slide, left, top + Inches(0.58), width, Inches(0.3), label, size=12, bold=True, color=CHARCOAL)
    _text(slide, left, top + Inches(0.9), width, Inches(0.3), f"{previous_pct:.1f}% → {current_pct:.1f}%", size=10, color=SLATE)


def _fmt(value):
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    return str(int(value))


def _trend_verb(row):
    if row.current > row.previous:
        return "rose"
    if row.current < row.previous:
        return "fell"
    return "held steady"


def _trend_phrase(row, noun):
    if row.current == row.previous:
        return f"{noun} held steady at {_fmt(row.current)}"
    return f"{noun} {_trend_verb(row)} to {_fmt(row.current)} (from {_fmt(row.previous)})"


def _bar_chart(slide, left, top, width, height, title, categories, series, *, palette=None, stacked=False):
    chart_data = CategoryChartData()
    chart_data.categories = categories
    for name, values in series:
        chart_data.add_series(name, values)
    chart_type = XL_CHART_TYPE.COLUMN_STACKED if stacked else XL_CHART_TYPE.COLUMN_CLUSTERED
    graphic_frame = slide.shapes.add_chart(chart_type, left, top, width, height, chart_data)
    chart = graphic_frame.chart
    chart.has_title = True
    chart.chart_title.text_frame.text = title
    chart.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(13)
    chart.chart_title.text_frame.paragraphs[0].runs[0].font.bold = True
    chart.chart_title.text_frame.paragraphs[0].runs[0].font.color.rgb = CHARCOAL
    colors = palette or [SLATE, NAVY, AMBER, RED]
    for index, plot_series in enumerate(chart.plots[0].series):
        plot_series.format.fill.solid()
        plot_series.format.fill.fore_color.rgb = colors[index % len(colors)]
    chart.has_legend = len(series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(10)
    value_axis = chart.value_axis
    value_axis.has_major_gridlines = True
    value_axis.major_gridlines.format.line.color.rgb = RGBColor(0xE8, 0xEA, 0xF0)
    value_axis.tick_labels.font.size = Pt(9)
    category_axis = chart.category_axis
    category_axis.tick_labels.font.size = Pt(9)
    category_axis.has_major_gridlines = False
    return graphic_frame


def _pie_chart(slide, left, top, width, height, title, categories, values):
    chart_data = CategoryChartData()
    chart_data.categories = categories
    chart_data.add_series("Share", values)
    graphic_frame = slide.shapes.add_chart(XL_CHART_TYPE.PIE, left, top, width, height, chart_data)
    chart = graphic_frame.chart
    chart.has_title = True
    chart.chart_title.text_frame.text = title
    chart.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(13)
    chart.chart_title.text_frame.paragraphs[0].runs[0].font.bold = True
    chart.chart_title.text_frame.paragraphs[0].runs[0].font.color.rgb = CHARCOAL
    palette = [NAVY, AMBER, GREEN, RED, SLATE, ICE, RGBColor(0x7C, 0x3A, 0xED), RGBColor(0x08, 0x91, 0xB2)]
    plot = chart.plots[0]
    for index, point in enumerate(plot.series[0].points):
        point.format.fill.solid()
        point.format.fill.fore_color.rgb = palette[index % len(palette)]
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.RIGHT
    chart.legend.include_in_layout = False
    chart.legend.font.size = Pt(9)
    plot.has_data_labels = True
    plot.data_labels.number_format = "0%"
    plot.data_labels.number_format_is_linked = False
    plot.data_labels.font.size = Pt(9)
    plot.data_labels.font.color.rgb = WHITE
    return graphic_frame


def _table(slide, left, top, width, height, headers, rows, *, col_widths=None):
    n_rows = len(rows) + 1
    n_cols = len(headers)
    graphic_frame = slide.shapes.add_table(n_rows, n_cols, left, top, width, height)
    table = graphic_frame.table
    if col_widths:
        for index, w in enumerate(col_widths):
            table.columns[index].width = w
    for col_index, header in enumerate(headers):
        cell = table.cell(0, col_index)
        cell.text = header
        cell.fill.solid()
        cell.fill.fore_color.rgb = NAVY
        paragraph = cell.text_frame.paragraphs[0]
        paragraph.font.size = Pt(11)
        paragraph.font.bold = True
        paragraph.font.color.rgb = WHITE
    for row_index, row in enumerate(rows, start=1):
        for col_index, value in enumerate(row):
            cell = table.cell(row_index, col_index)
            cell.text = str(value)
            cell.fill.solid()
            cell.fill.fore_color.rgb = LIGHT_BG if row_index % 2 == 0 else WHITE
            paragraph = cell.text_frame.paragraphs[0]
            paragraph.font.size = Pt(11)
            paragraph.font.color.rgb = CHARCOAL
    return graphic_frame


def _numbered_circle(slide, left, top, diameter, number, color=NAVY):
    circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, left, top, diameter, diameter)
    circle.fill.solid()
    circle.fill.fore_color.rgb = color
    circle.line.fill.background()
    circle.shadow.inherit = False
    tf = circle.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    paragraph = tf.paragraphs[0]
    paragraph.alignment = PP_ALIGN.CENTER
    run = paragraph.add_run()
    run.text = str(number)
    run.font.size = Pt(16)
    run.font.bold = True
    run.font.color.rgb = WHITE
    return circle


def _title_stat(slide, left, top, width, value, label):
    _text(slide, left, top, width, Inches(0.9), str(value), size=48, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    _text(slide, left, top + Inches(0.95), width, Inches(0.4), label, size=13, color=ICE, align=PP_ALIGN.CENTER)


def _headline_sentence(data):
    """The single most important finding, phrased like the reference deck's title-cased headlines."""
    present_row = next(r for r in data.headline_comparison if r.label == "Present Records")
    verb = "ROSE" if present_row.current >= present_row.previous else "FELL"
    delta = abs(present_row.current - present_row.previous)
    return (
        f"PRESENT ATTENDANCE {verb} BY {_fmt(delta)} RECORDS ({_growth_label(present_row.growth_pct)}) "
        f"— {_fmt(present_row.current)} PRESENT RECORDS THIS WEEK"
    )


def build_weekly_report_pptx(data, *, company_name="Rotic Aluminium"):
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    date_range = f"{data.week_start.strftime('%d %b')} - {data.week_end.strftime('%d %b %Y')}"
    present_row = next(r for r in data.headline_comparison if r.label == "Present Records")
    leave_row = next(r for r in data.headline_comparison if r.label == "Leave Requests")
    hires_row = next(r for r in data.headline_comparison if r.label == "New Hires")
    meals_row = next(r for r in data.headline_comparison if r.label == "Meal Tickets")
    absent_row = next(r for r in data.headline_comparison if r.label == "Absent Records")
    top_leave = data.leave_type_comparison[0] if data.leave_type_comparison else None
    headcount_growth = ComparisonRow("Active Headcount", data.previous_active_employees, data.active_employees)
    meal_cost_growth = ComparisonRow("Meal Cost", float(data.previous_meal_total_cost), float(data.meal_total_cost))
    understaffed = [row for row in data.department_approved if row.diff < 0]

    # --- Slide 1: Title ---
    slide = _blank(prs)
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, HEADER_BG)
    if LOGO_PATH.exists():
        slide.shapes.add_picture(str(LOGO_PATH), Inches(0.6), Inches(0.4), height=Inches(0.5))
    _text(slide, Inches(0.8), Inches(0.6), Inches(11.7), Inches(0.4), date_range.upper(), size=15, bold=True, color=ICE, align=PP_ALIGN.CENTER)
    _text(slide, Inches(0.8), Inches(1.05), Inches(11.7), Inches(0.9), f"WEEKLY HR REPORT FOR WEEK {data.week_number}", size=30, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    _title_stat(slide, Inches(0.8), Inches(3.1), Inches(2.7), data.active_employees, "Total Headcount")
    _title_stat(slide, Inches(3.8), Inches(3.1), Inches(2.7), f"{'+' if data.net_movement >= 0 else ''}{data.net_movement}", "Net Movement")
    _title_stat(slide, Inches(6.8), Inches(3.1), Inches(2.7), f"{data.retention_rate:.1f}%", "Retention Rate")
    _title_stat(slide, Inches(9.8), Inches(3.1), Inches(2.7), f"{data.hire_rate:.1f}%", "Hire Rate")
    _text(slide, Inches(0.8), Inches(6.5), Inches(11.7), Inches(0.4), f"Prepared By: Rotic HRM System · Generated {data.generated_at.strftime('%d %b %Y, %H:%M')}", size=12, color=ICE, align=PP_ALIGN.CENTER)

    # --- Slide 2: Executive Summary (COO / MD Summary) ---
    slide = _blank(prs)
    _header(slide, "HR DASHBOARD — COO / MD SUMMARY", f"Week {data.week_number} · {date_range} · Human Resources", page=2)
    _stat_row(slide, Inches(1.55), [
        (data.active_employees, "Total Headcount", NAVY),
        (f"{'+' if data.net_movement >= 0 else ''}{data.net_movement}", "Staff Movement", GREEN if data.net_movement >= 0 else RED),
        (f"{data.retention_rate:.1f}%", "Retention Rate", NAVY),
        (f"{data.hire_rate:.1f}%", "Hire Rate", NAVY),
        (len(understaffed), "Key Vacancies", AMBER if understaffed else SLATE),
    ], card_w=Inches(2.25), gap=Inches(0.2))

    highlights = []
    if data.previous_active_employees:
        highlights.append(f"✓  Headcount {_trend_verb(headcount_growth)} {_fmt(headcount_growth.previous)}→{_fmt(headcount_growth.current)} ({_growth_label(headcount_growth.growth_pct)}). Retention rate: {data.retention_rate:.1f}%.")
    else:
        highlights.append(f"✓  Active headcount stands at {data.active_employees}. Retention rate: {data.retention_rate:.1f}%.")
    highlights.append(f"✓  {len(data.new_hires)} new hire{'s' if len(data.new_hires) != 1 else ''} this week. Hire rate {data.hire_rate:.1f}%. Attrition rate: {data.attrition_rate:.1f}%.")
    if data.meal_total_cost:
        highlights.append(f"{'✓' if meal_cost_growth.growth_pct <= 10 else '⚠'}  Meal cost {_trend_verb(meal_cost_growth)} {_growth_label(meal_cost_growth.growth_pct)} to {_currency(data.meal_total_cost)}.")
    if understaffed:
        worst = min(understaffed, key=lambda row: row.diff)
        highlights.append(f"⚠  {worst.name} is understaffed by {abs(worst.diff)} against its approved headcount of {worst.approved}.")
    if data.offence_count:
        highlights.append(f"\U0001f534  {data.offence_count} disciplinary case{'s' if data.offence_count != 1 else ''} recorded this week ({_currency(data.offence_total_amount)}).")
    if len(highlights) < 5 and data.accommodation_company.capacity:
        rate = data.accommodation_company.occupancy_rate
        if rate >= 90:
            highlights.append(f"⚠  Company accommodation is at {rate:.0f}% occupancy — {data.accommodation_company.vacant} bedspace(s) left.")

    actions = []
    for row in sorted(understaffed, key=lambda r: r.diff)[:2]:
        actions.append(f"Advance recruitment for {row.name} — {abs(row.diff)} short of the approved headcount of {row.approved}.")
    if data.leave_pending:
        actions.append(f"Clear {data.leave_pending} pending leave request{'s' if data.leave_pending != 1 else ''}.")
    if data.offence_count:
        actions.append(f"Review {data.offence_count} disciplinary case{'s' if data.offence_count != 1 else ''} raised this week ({_currency(data.offence_total_amount)}).")
    if data.accommodation_company.capacity and data.accommodation_company.occupancy_rate >= 90:
        actions.append(f"Plan additional bedspace — company accommodation at {data.accommodation_company.occupancy_rate:.0f}% occupancy.")
    if data.top_absentee_departments:
        top_dept = data.top_absentee_departments[0]
        actions.append(f"Investigate absenteeism in {top_dept.name} ({top_dept.count} absences this week).")
    if not actions:
        actions.append("No actions flagged this week — all tracked metrics are within normal range.")

    _text(slide, Inches(0.6), Inches(3.15), Inches(6.0), Inches(0.35), "KEY HIGHLIGHTS", size=13, bold=True, color=NAVY)
    _text(slide, Inches(0.6), Inches(3.6), Inches(6.0), Inches(3.3), "\n".join(highlights), size=11.5, color=CHARCOAL, line_spacing=1.25)
    _text(slide, Inches(6.9), Inches(3.15), Inches(6.0), Inches(0.35), "ACTIONS REQUIRED", size=13, bold=True, color=NAVY)
    _text(slide, Inches(6.9), Inches(3.6), Inches(6.0), Inches(3.3), "\n".join(f"{i + 1}. {a}" for i, a in enumerate(actions)), size=11.5, color=CHARCOAL, line_spacing=1.25)
    _footer(slide, "COO / MD Summary")

    # --- Slide 3: Employee Overview & Key HR Metrics ---
    slide = _blank(prs)
    _header(slide, "EMPLOYEE OVERVIEW & KEY HR METRICS", f"Hiring · Retention · Attrition rates · Week {data.week_number}", page=3)
    _stat_row(slide, Inches(1.55), [
        (data.previous_active_employees, "Opening Headcount", NAVY),
        (len(data.new_hires), "New Hires This Week", GREEN),
        (len(data.exits), "Exits This Week", RED),
    ], card_w=Inches(3.8), gap=Inches(0.3))
    _stat_row(slide, Inches(3.0), [
        (data.active_employees, "Closing Headcount", NAVY),
        (f"{data.retention_rate:.1f}%", "Retention Rate", GREEN),
        (f"{data.attrition_rate:.1f}%", "Attrition Rate", RED if data.attrition_rate > 0 else SLATE),
    ], card_w=Inches(3.8), gap=Inches(0.3))
    _text(
        slide, Inches(0.6), Inches(4.6), Inches(12.1), Inches(1.2),
        f"Hire rate this week: {data.hire_rate:.1f}% ({len(data.new_hires)} new hires ÷ {data.active_employees} closing headcount × 100).",
        size=13, bold=True, color=NAVY,
    )
    _rect(slide, Inches(0.6), Inches(5.4), Inches(12.1), Inches(1.15), LIGHT_BG)
    _text(
        slide, Inches(0.8), Inches(5.52), Inches(11.7), Inches(0.95),
        "RATE FORMULAS\nRetention Rate = (Closing HC − Exits) ÷ Closing HC × 100   ·   Hire Rate = New Hires ÷ Closing HC × 100   ·   "
        "Attrition Rate = Exits ÷ Opening HC × 100",
        size=10.5, color=SLATE, line_spacing=1.3,
    )
    _footer(slide, "Employee Overview")

    # --- Slide 4: Daily Breakdown (Attendance) ---
    slide = _blank(prs)
    peak_present = max(data.attendance_by_day, key=lambda d: d.present, default=None)
    peak_absent = max(data.attendance_by_day, key=lambda d: d.absent, default=None)
    if peak_present and peak_present.present > 0:
        headline3 = f"ATTENDANCE PEAKED ON {peak_present.date.strftime('%A').upper()} WITH {peak_present.present} PRESENT RECORDS"
    else:
        headline3 = "DAILY ATTENDANCE BREAKDOWN"
    _header(slide, headline3, None, page=4)
    day_rows = [(d.date.strftime("%d %b"), d.date.strftime("%A"), d.present, d.late, d.absent) for d in data.attendance_by_day]
    total_present = sum(d.present for d in data.attendance_by_day)
    total_late = sum(d.late for d in data.attendance_by_day)
    total_absent = sum(d.absent for d in data.attendance_by_day)
    day_rows.append(("", "Total", total_present, total_late, total_absent))
    _table(
        slide, Inches(0.6), Inches(1.55), Inches(5.6), Inches(0.35 * len(day_rows)),
        ["Date", "Day", "Present", "Late", "Absent"], day_rows,
        col_widths=[Inches(1.1), Inches(1.6), Inches(1.0), Inches(0.9), Inches(1.0)],
    )
    observations = []
    if peak_present and peak_present.present > 0:
        observations.append(f"{peak_present.date.strftime('%A')} had the most present records, with {peak_present.present}.")
    if peak_absent and peak_absent.absent > 0:
        observations.append(f"{peak_absent.date.strftime('%A')} recorded the most absences, with {peak_absent.absent}.")
    if not observations:
        observations.append("No attendance records were logged for this week yet.")
    _text(slide, Inches(0.6), Inches(1.55) + Inches(0.35 * len(day_rows)) + Inches(0.3), Inches(5.6), Inches(1.2), "Key Observations", size=13, bold=True, color=NAVY)
    _text(slide, Inches(0.6), Inches(1.55) + Inches(0.35 * len(day_rows)) + Inches(0.65), Inches(5.6), Inches(1.2), " ".join(observations), size=11, color=CHARCOAL, line_spacing=1.15)
    if data.attendance_by_day:
        _bar_chart(
            slide, Inches(6.5), Inches(1.55), Inches(6.2), Inches(2.6),
            "Daily Present Records",
            [d.date.strftime("%a %d") for d in data.attendance_by_day],
            [("Present", [d.present for d in data.attendance_by_day])],
            palette=[GREEN],
        )
        _bar_chart(
            slide, Inches(6.5), Inches(4.25), Inches(6.2), Inches(2.6),
            "Daily Absent Records",
            [d.date.strftime("%a %d") for d in data.attendance_by_day],
            [("Absent", [d.absent for d in data.attendance_by_day])],
            palette=[RED],
        )
    _footer(slide, "Daily Breakdown")

    # --- Slide 5: Top Departments (by attendance - no historical department headcount exists) ---
    slide = _blank(prs)
    comparable = [row for row in data.department_comparison if row.previous >= 5]
    leader = data.department_comparison[0] if data.department_comparison else None
    grower = max(comparable, key=lambda r: r.growth_pct, default=None)
    decliner = min(comparable, key=lambda r: r.growth_pct, default=None)
    headline4 = f"{leader.label.upper()} LED ATTENDANCE THIS WEEK WITH {_fmt(leader.current)} PRESENT RECORDS" if leader else "TOP DEPARTMENTS BY ATTENDANCE"
    _header(slide, headline4, f"Present records by department · Week {data.week_number}", page=5)
    if data.department_comparison:
        _bar_chart(
            slide, Inches(0.6), Inches(1.55), Inches(8.2), Inches(4.9),
            "Present Records by Department: Week Before vs Current Week",
            [row.label for row in data.department_comparison],
            [
                ("Week Before", [row.previous for row in data.department_comparison]),
                ("Current Week", [row.current for row in data.department_comparison]),
            ],
            palette=[SLATE, NAVY],
        )
    narrative_parts = []
    if grower and decliner and grower.label != decliner.label:
        narrative_parts.append(f"{grower.label} grew the fastest week-over-week ({_growth_label(grower.growth_pct)}), while {decliner.label} declined the most ({_growth_label(decliner.growth_pct)}).")
    elif grower:
        narrative_parts.append(f"{grower.label} grew the fastest week-over-week ({_growth_label(grower.growth_pct)}).")
    else:
        narrative_parts.append("Not enough prior-week data exists yet to compare department growth.")
    _text(slide, Inches(9.1), Inches(1.7), Inches(3.6), Inches(0.35), "Department Notes", size=13, bold=True, color=NAVY)
    _text(slide, Inches(9.1), Inches(2.1), Inches(3.6), Inches(3.5), " ".join(narrative_parts), size=11, color=CHARCOAL, line_spacing=1.15)
    _footer(slide, "Top Departments")

    # --- Slide 6: Current Headcount vs Approved Headcount by Department ---
    slide = _blank(prs)
    _header(slide, "COMPARATIVE ANALYSIS — CURRENT STAFF VS DEPARTMENT NEEDS", f"Approved headcount vs actual · Surplus & pending hires · Week {data.week_number}", page=6)
    _stat_row(slide, Inches(1.55), [
        (data.active_employees, "Current Total", NAVY),
        (data.approved_headcount_total, "Approved Headcount", SLATE),
        (data.pending_hires_total, "Pending Hires", AMBER if data.pending_hires_total else GREEN),
        (data.surplus_employees_total, "Surplus Employees", SLATE),
    ], card_w=Inches(2.8), gap=Inches(0.22))
    if data.department_approved:
        chart_rows = data.department_approved[:12]
        _bar_chart(
            slide, Inches(0.6), Inches(3.0), Inches(7.4), Inches(4.0),
            "Current Headcount vs Approved Headcount by Department",
            [row.name for row in chart_rows],
            [("Current", [row.current for row in chart_rows]), ("Approved", [row.approved for row in chart_rows])],
            palette=[NAVY, SLATE],
        )
        _table(
            slide, Inches(8.3), Inches(3.0), Inches(4.4), Inches(0.3 * (min(len(data.department_approved), 12) + 1)),
            ["Dept", "Current", "Approved", "Status"],
            [(row.name, row.current, row.approved, row.status) for row in data.department_approved[:12]],
            col_widths=[Inches(1.9), Inches(0.85), Inches(0.9), Inches(0.8)],
        )
    else:
        _text(slide, Inches(0.6), Inches(3.0), Inches(11), Inches(0.4), "No department has an approved headcount target set yet (Department → Required Staff).", size=12, color=SLATE, italic=True)
    _footer(slide, "Department Needs")

    # --- Slide 7: Leave Type Breakdown ---
    slide = _blank(prs)
    if top_leave and top_leave.current > 0:
        share = top_leave.current / max(data.leave_submitted, 1) * 100
        headline5 = f"{top_leave.label.upper()} REMAINS THE LEADING LEAVE TYPE AT {_fmt(top_leave.current)} REQUESTS, {_growth_label(top_leave.growth_pct)} WEEK-OVER-WEEK"
    else:
        headline5 = "LEAVE REQUESTS BY TYPE"
    _header(slide, headline5, None, page=7)
    if data.leave_type_comparison:
        _table(
            slide, Inches(0.6), Inches(1.55), Inches(6.6), Inches(0.35 * (len(data.leave_type_comparison) + 1)),
            ["Leave Type", "Week Before", "Current Week", "Growth"],
            [(row.label, _fmt(row.previous), _fmt(row.current), _growth_label(row.growth_pct)) for row in data.leave_type_comparison],
            col_widths=[Inches(3.0), Inches(1.2), Inches(1.2), Inches(1.2)],
        )
        current_only = [row for row in data.leave_type_comparison if row.current > 0]
        if current_only:
            _pie_chart(
                slide, Inches(7.5), Inches(1.55), Inches(5.2), Inches(3.2),
                "This Week's Leave Requests by Type",
                [row.label for row in current_only],
                [row.current for row in current_only],
            )
    else:
        _text(slide, Inches(0.6), Inches(1.55), Inches(6), Inches(0.4), "No leave requests were submitted this week.", size=12, color=SLATE, italic=True)
    if top_leave and top_leave.current > 0:
        implication = (
            f"HR Implication: {top_leave.label} accounted for {share:.0f}% of all leave requests this week. "
            f"{'Plan cover for the affected departments accordingly.' if top_leave.growth_pct > 0 else 'Demand has eased from the week before.'}"
        )
        _text(slide, Inches(0.6), Inches(5.1), Inches(11.9), Inches(0.9), implication, size=11.5, color=CHARCOAL, line_spacing=1.15)
    _footer(slide, "Leave")

    # --- Slide 8: Staff Movement (New Hires & Exits Detail) ---
    slide = _blank(prs)
    affected_departments = {p.department for p in (data.new_hires + data.exits) if p.department and p.department != "-"}
    headline6 = f"{len(data.new_hires)} NEW HIRE{'S' if len(data.new_hires) != 1 else ''} AND {len(data.exits)} EXIT{'S' if len(data.exits) != 1 else ''} THIS WEEK — WORKFORCE MOVEMENT DETAIL"
    _header(slide, headline6, None, page=8)
    _stat_row(slide, Inches(1.55), [
        (len(data.new_hires), "New Hires", GREEN),
        (len(data.exits), "Exits", RED),
        (("+" if data.net_movement >= 0 else "") + str(data.net_movement), "Net Change", NAVY),
        (len(affected_departments), "Departments Affected", SLATE),
        (data.active_employees, "Active Employees", NAVY),
    ])
    movements = sorted(
        [(p, "Hire") for p in data.new_hires] + [(p, "Exit") for p in data.exits],
        key=lambda pair: pair[0].date,
    )
    max_rows = 12
    shown = movements[:max_rows]
    if shown:
        _table(
            slide, Inches(0.6), Inches(3.05), Inches(12.1), Inches(0.35 * (len(shown) + 1)),
            ["Staff No.", "Name", "Department", "Type", "Date"],
            [(p.employee_id, p.name, p.department, kind, p.date.strftime("%d %b %Y")) for p, kind in shown],
            col_widths=[Inches(1.5), Inches(3.5), Inches(2.7), Inches(1.4), Inches(2.0)],
        )
        if len(movements) > max_rows:
            _text(slide, Inches(0.6), Inches(3.05) + Inches(0.35 * (len(shown) + 1)) + Inches(0.05), Inches(6), Inches(0.3),
                  f"+ {len(movements) - max_rows} more — see the full list in the app.", size=10, color=SLATE, italic=True)
    else:
        _text(slide, Inches(0.6), Inches(3.05), Inches(6), Inches(0.4), "No hires or exits were recorded this week.", size=12, color=SLATE, italic=True)
    _footer(slide, "Workforce Movement")

    # --- Slide 9: Gender Ratio & Distribution ---
    slide = _blank(prs)
    gender_total = data.gender_male + data.gender_female
    male_pct = (data.gender_male / data.active_employees * 100) if data.active_employees else 0.0
    female_pct = (data.gender_female / data.active_employees * 100) if data.active_employees else 0.0
    _header(slide, "EMPLOYEE GENDER RATIO & DISTRIBUTION", f"Overall ratio · By department · Active workforce snapshot · Week {data.week_number}", page=9)
    _stat_row(slide, Inches(1.55), [
        (data.gender_male, "Total Male", NAVY),
        (data.gender_female, "Total Female", RGBColor(0x7C, 0x3A, 0xED)),
        (f"{male_pct:.1f}%", "Male Ratio", NAVY),
        (f"{female_pct:.1f}%", "Female Ratio", RGBColor(0x7C, 0x3A, 0xED)),
    ], card_w=Inches(2.8), gap=Inches(0.22))
    if gender_total:
        _pie_chart(
            slide, Inches(0.6), Inches(3.0), Inches(4.6), Inches(3.9),
            "Overall Gender Ratio", ["Male", "Female"], [data.gender_male, data.gender_female],
        )
    if data.gender_by_department:
        _bar_chart(
            slide, Inches(5.5), Inches(3.0), Inches(7.2), Inches(3.9),
            "Gender Distribution by Department",
            [row.name for row in data.gender_by_department],
            [("Male", [row.male for row in data.gender_by_department]), ("Female", [row.female for row in data.gender_by_department])],
            palette=[NAVY, RGBColor(0x7C, 0x3A, 0xED)],
        )
    if data.gender_unspecified:
        _text(slide, Inches(0.6), Inches(7.0), Inches(11), Inches(0.3),
              f"{data.gender_unspecified} active employee(s) have no gender recorded and are excluded from the ratio above.", size=10, color=SLATE, italic=True)
    _footer(slide, "Gender")

    # --- Slide 10: Accommodation Occupancy ---
    slide = _blank(prs)
    _header(slide, "ACCOMMODATION — OCCUPANCY OVERVIEW", f"Company vs external lodging · Gender split · Week {data.week_number}", page=10)
    _stat_row(slide, Inches(1.55), [
        (f"{data.accommodation_company.occupied}/{data.accommodation_company.capacity}", "Company Occupancy", NAVY),
        (f"{data.accommodation_company.occupancy_rate:.1f}%", "Company Occ. Rate", NAVY),
        (f"{data.accommodation_external.occupied}/{data.accommodation_external.capacity}", "External Occupancy", SLATE),
        (data.accommodation_company.vacant + data.accommodation_external.vacant, "Vacant Spaces", GREEN),
    ], card_w=Inches(2.8), gap=Inches(0.22))
    if data.accommodation_by_building:
        _bar_chart(
            slide, Inches(0.6), Inches(3.0), Inches(7.0), Inches(3.9),
            "Capacity vs Occupied by Building",
            [b.name for b in data.accommodation_by_building],
            [("Capacity", [b.capacity for b in data.accommodation_by_building]), ("Occupied", [b.occupied for b in data.accommodation_by_building])],
            palette=[SLATE, NAVY],
        )
        _table(
            slide, Inches(7.9), Inches(3.0), Inches(4.8), Inches(0.3 * (len(data.accommodation_by_building) + 1)),
            ["Building", "Cap.", "Occ.", "Vacant"],
            [(b.name, b.capacity, b.occupied, b.vacant) for b in data.accommodation_by_building],
            col_widths=[Inches(2.3), Inches(0.8), Inches(0.8), Inches(0.95)],
        )
    else:
        _text(slide, Inches(0.6), Inches(3.0), Inches(11), Inches(0.4), "No accommodation buildings/rooms are set up yet.", size=12, color=SLATE, italic=True)
    if data.accommodation_company.occupied:
        _text(slide, Inches(0.6), Inches(7.0), Inches(11), Inches(0.3),
              f"Company accommodation gender split: {data.accommodation_company_male}M / {data.accommodation_company_female}F.", size=10, color=SLATE)
    _footer(slide, "Accommodation")

    # --- Slide 11: Meal Ticket Report ---
    slide = _blank(prs)
    meal_growth_label = _growth_label(meal_cost_growth.growth_pct) if data.previous_meal_total_cost else "N/M"
    _header(slide, "MEAL TICKET REPORT", f"Week {data.week_number} · {_currency(data.meal_total_cost)} total · {data.meal_collections} tickets · {meal_growth_label} on the week before", page=11)
    _stat_row(slide, Inches(1.55), [
        (_currency(data.meal_total_cost), "Total Cost This Week", NAVY),
        (_currency(data.previous_meal_total_cost), "Total Cost Week Before", SLATE),
        (meal_growth_label, "WoW Change", _growth_color(meal_cost_growth.growth_pct) if data.previous_meal_total_cost else SLATE),
        (_currency(data.meal_cost_per_ticket), "Cost per Ticket", NAVY),
    ], card_w=Inches(2.8), gap=Inches(0.22))
    if any(d.day_shift or d.night_shift for d in data.meal_by_day_and_shift):
        _bar_chart(
            slide, Inches(0.6), Inches(3.0), Inches(7.0), Inches(3.9),
            "Daily Meal Ticket Distribution by Shift",
            [d.date.strftime("%a %d") for d in data.meal_by_day_and_shift],
            [
                ("Day Shift", [d.day_shift for d in data.meal_by_day_and_shift]),
                ("Night Shift", [d.night_shift for d in data.meal_by_day_and_shift]),
            ],
            palette=[AMBER, NAVY], stacked=True,
        )
    else:
        _text(slide, Inches(0.6), Inches(3.0), Inches(7.0), Inches(0.4), "No meal tickets were collected this week.", size=12, color=SLATE, italic=True)
    _bar_chart(
        slide, Inches(7.9), Inches(3.0), Inches(4.8), Inches(3.9),
        "3-Week Meal Cost Trend (₦)",
        [label for label, _ in data.meal_cost_trend],
        [("Meal Cost", [float(cost) for _, cost in data.meal_cost_trend])],
        palette=[NAVY],
    )
    _footer(slide, "Meals")

    # --- Slide 12: Disciplinary Actions (Offences) ---
    slide = _blank(prs)
    _header(slide, "DISCIPLINARY ACTIONS", f"Week {data.week_number} · From the Offences module — no separate grievance log exists in HRM yet", page=12)
    _stat_row(slide, Inches(1.55), [
        (data.offence_count, "Cases This Week", AMBER if data.offence_count else GREEN),
        (_currency(data.offence_total_amount), "Total Amount", NAVY),
        (len(data.offence_by_type), "Offence Types Involved", SLATE),
    ], card_w=Inches(3.8), gap=Inches(0.3))
    if data.offence_by_type:
        _bar_chart(
            slide, Inches(0.6), Inches(3.0), Inches(6.8), Inches(3.9),
            "Cases by Offence Type",
            [row.name for row in data.offence_by_type],
            [("Cases", [row.count for row in data.offence_by_type])],
            palette=[AMBER],
        )
    if data.offence_by_status:
        _table(
            slide, Inches(7.7), Inches(3.0), Inches(5.0), Inches(0.35 * (len(data.offence_by_status) + 1)),
            ["Status", "Cases"],
            [(row.name, row.count) for row in data.offence_by_status],
            col_widths=[Inches(3.4), Inches(1.7)],
        )
    if not data.offence_count:
        _text(slide, Inches(0.6), Inches(3.0), Inches(11), Inches(0.4), "No disciplinary cases were recorded this week.", size=12, color=SLATE, italic=True)
    _footer(slide, "Disciplinary")

    # --- Slide 13: Recruitment Status (manual - HRM has no recruitment/ATS module) ---
    slide = _blank(prs)
    _header(slide, "RECRUITMENT STATUS", f"Week {data.week_number} · Vacancies below are computed from approved headcount; the pipeline must be entered by hand", page=13)
    _manual_entry_badge(slide)
    _stat_row(slide, Inches(1.55), [
        ("[X]", "Applications Reviewed", SLATE),
        ("[X]", "Interviewed", SLATE),
        (len(understaffed), "Roles Vacant", AMBER if understaffed else GREEN),
        ("[X]", "Offers Made", SLATE),
    ], card_w=Inches(2.8), gap=Inches(0.22))
    if understaffed:
        _table(
            slide, Inches(0.6), Inches(3.0), Inches(12.1), Inches(0.35 * (min(len(understaffed), 8) + 1)),
            ["Department", "Vacant", "Applications", "Interviewed", "Status"],
            [(row.name, abs(row.diff), "[X]", "[X]", "Open") for row in understaffed[:8]],
            col_widths=[Inches(4.0), Inches(1.8), Inches(2.1), Inches(2.1), Inches(2.1)],
        )
        table_bottom = Inches(3.0) + Inches(0.35 * (min(len(understaffed), 8) + 1)) + Inches(0.2)
    else:
        _text(slide, Inches(0.6), Inches(3.0), Inches(11), Inches(0.4), "No department is currently below its approved headcount.", size=12, color=SLATE, italic=True)
        table_bottom = Inches(3.5)
    _manual_box(
        slide, Inches(0.6), table_bottom, Inches(12.1), Inches(1.3),
        "COMPLETE BEFORE SENDING: HRM does not track applications, screening or interview stages. "
        "Enter real figures per role from the recruitment tracker, replace every [X], and add an analysis line "
        "summarising pipeline progress and expected fill dates.",
    )
    _footer(slide, "Recruitment")

    # --- Slide 14: Training Activity (manual - HRM has no training/LMS module) ---
    slide = _blank(prs)
    _header(slide, "TRAINING ACTIVITY", f"Week {data.week_number} · New-staff onboarding count is real; sessions must be entered by hand", page=14)
    _manual_entry_badge(slide)
    _stat_row(slide, Inches(1.55), [
        ("[X]", "Total Trained", SLATE),
        ("[X]", "Sessions Held", SLATE),
        (len(data.new_hires), "New Staff Onboarded", GREEN),
        ("[X]%", "Training Attendance Rate", SLATE),
    ], card_w=Inches(2.8), gap=Inches(0.22))
    _table(
        slide, Inches(0.6), Inches(3.0), Inches(12.1), Inches(1.75),
        ["Programme", "Session", "Target Participants", "Actual Attendees", "Status"],
        [
            ("[Programme name]", "[Session X of Y]", "[Target]", "[Actual]", "[Held / Postponed]"),
            ("New Hire Onboarding", "Day 1 Induction", f"{len(data.new_hires)} new staff", str(len(data.new_hires)), "[Completed / Pending]"),
            ("[Programme name]", "[As scheduled]", "[Target]", "[Actual]", "[Held / Postponed]"),
        ],
        col_widths=[Inches(3.2), Inches(2.2), Inches(2.5), Inches(2.1), Inches(2.1)],
    )
    _manual_box(
        slide, Inches(0.6), Inches(5.0), Inches(12.1), Inches(1.3),
        "COMPLETE BEFORE SENDING: HRM does not track training programmes or session attendance. "
        "Add one row per programme run this week, replace every [X], and confirm which of this week's "
        f"{len(data.new_hires)} new hire(s) completed Day 1 induction.",
    )
    _footer(slide, "Training")

    # --- Slide 15: Recommendations ---
    slide = _blank(prs)
    _header(slide, "RECOMMENDATIONS", None, page=15)

    recommendations = []
    if data.top_absentee_departments:
        top_dept = data.top_absentee_departments[0]
        recommendations.append(("Attendance", f"{top_dept.name} recorded the most absences this week, with {top_dept.count}. Investigate attendance patterns there and confirm rosters are being followed."))
    else:
        recommendations.append(("Attendance", "No department stood out for absences this week — attendance patterns look even across departments."))

    if top_leave and top_leave.current > 0 and top_leave.growth_pct > 0:
        recommendations.append(("Leave Demand", f"Demand for {top_leave.label} grew {_growth_label(top_leave.growth_pct)} this week ({_fmt(top_leave.previous)} → {_fmt(top_leave.current)} requests). Plan cover for the departments most affected."))
    elif top_leave and top_leave.current > 0:
        recommendations.append(("Leave Demand", f"{top_leave.label} remains the most requested leave type at {_fmt(top_leave.current)} requests, though demand has eased from the week before."))
    else:
        recommendations.append(("Leave Demand", "Leave activity was quiet this week — no type stood out."))

    if understaffed:
        worst = min(understaffed, key=lambda row: row.diff)
        recommendations.append(("Department Staffing", f"{worst.name} is the most understaffed department, {abs(worst.diff)} short of its approved headcount of {worst.approved}. Prioritise recruitment or reassign surplus staff from over-target departments before opening new roles."))
    elif data.leave_pending > 0:
        recommendations.append(("Approval Backlog", f"{data.leave_pending} leave request{'s' if data.leave_pending != 1 else ''} still await{'s' if data.leave_pending == 1 else ''} a decision. Clear the approval queue before it grows further."))
    else:
        recommendations.append(("Approval Backlog", "The leave approval queue is fully cleared — no pending requests carried over this week."))

    if data.offence_count:
        recommendations.append(("Disciplinary", f"{data.offence_count} disciplinary case{'s' if data.offence_count != 1 else ''} were recorded this week, totalling {_currency(data.offence_total_amount)}. Review with department heads and confirm each case follows the standard process."))

    top = Inches(1.7)
    palette = [NAVY, AMBER, GREEN, RED]
    for index, (heading, text) in enumerate(recommendations[:4]):
        _numbered_circle(slide, Inches(0.6), top, Inches(0.5), index + 1, palette[index % len(palette)])
        _text(slide, Inches(1.3), top - Inches(0.02), Inches(11.2), Inches(0.35), heading, size=15, bold=True, color=NAVY)
        _text(slide, Inches(1.3), top + Inches(0.38), Inches(11.2), Inches(0.9), text, size=12.5, color=CHARCOAL, line_spacing=1.2)
        top = top + Inches(1.35)
    _footer(slide, "Recommendations")

    output = io.BytesIO()
    prs.save(output)
    return output.getvalue()
