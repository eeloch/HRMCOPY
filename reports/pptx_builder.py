"""Builds the Weekly HR Report slide deck (.pptx) from a WeeklyReportData. No file is written to disk -
callers get bytes back, to stream straight out of an API response.

Structure mirrors a narrative analyst report (headline finding, week-over-week comparison, daily
breakdown, top movers, category share, detail table, recommendations) rather than a plain stat dashboard.
"""

import io

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

NAVY = RGBColor(0x1E, 0x27, 0x61)
ICE = RGBColor(0xCA, 0xDC, 0xFC)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CHARCOAL = RGBColor(0x27, 0x2D, 0x3B)
SLATE = RGBColor(0x64, 0x74, 0x8B)
LIGHT_BG = RGBColor(0xF4, 0xF6, 0xFB)
GREEN = RGBColor(0x05, 0x96, 0x69)
AMBER = RGBColor(0xD9, 0x77, 0x06)
RED = RGBColor(0xDC, 0x26, 0x26)

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def _blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _rect(slide, left, top, width, height, color, *, line_color=None):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    if line_color is not None:
        shape.line.color.rgb = line_color
        shape.line.width = Pt(0.75)
    else:
        shape.line.fill.background()
    shape.shadow.inherit = False
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


def _title_bar(slide, title, subtitle=None):
    _text(slide, Inches(0.6), Inches(0.3), Inches(12.1), Inches(0.75), title, size=24, bold=True, color=NAVY, line_spacing=1.0)
    if subtitle:
        _text(slide, Inches(0.6), Inches(1.02), Inches(12.1), Inches(0.35), subtitle, size=12, color=SLATE)


def _stat_card(slide, left, top, width, height, value, label, color=NAVY):
    _rect(slide, left, top, width, height, WHITE, line_color=RGBColor(0xE2, 0xE6, 0xF0))
    _text(slide, left + Inches(0.15), top + Inches(0.12), width - Inches(0.3), height - Inches(0.75), str(value), size=30, bold=True, color=color)
    _text(slide, left + Inches(0.15), top + height - Inches(0.5), width - Inches(0.3), Inches(0.4), label, size=10, color=SLATE)


def _stat_row(slide, top, items, card_w=Inches(1.95), gap=Inches(0.2), start_left=Inches(0.6), card_h=Inches(1.2)):
    left = start_left
    for value, label, color in items:
        _stat_card(slide, left, top, card_w, card_h, value, label, color)
        left = left + card_w + gap


def _growth_color(pct):
    if pct > 0:
        return GREEN
    if pct < 0:
        return RED
    return SLATE


def _growth_label(pct):
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


def _bar_chart(slide, left, top, width, height, title, categories, series, *, palette=None):
    chart_data = CategoryChartData()
    chart_data.categories = categories
    for name, values in series:
        chart_data.add_series(name, values)
    graphic_frame = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, left, top, width, height, chart_data)
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
    rate_growth = data.attendance_rate - data.previous_attendance_rate

    # --- Slide 1: Title ---
    slide = _blank(prs)
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, NAVY)
    _text(slide, Inches(0.8), Inches(0.6), Inches(11.7), Inches(0.4), date_range.upper(), size=15, bold=True, color=ICE, align=PP_ALIGN.CENTER)
    _text(slide, Inches(0.8), Inches(1.05), Inches(11.7), Inches(0.9), f"WEEKLY HR REPORT FOR WEEK {data.week_number}", size=30, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    _title_stat(slide, Inches(0.9), Inches(3.1), Inches(3.6), f"{data.attendance_rate:.1f}%", "Attendance Rate")
    _title_stat(slide, Inches(4.9), Inches(3.1), Inches(3.6), f"{'+' if rate_growth >= 0 else ''}{rate_growth:.1f} pts", "Attendance Rate WoW")
    _title_stat(slide, Inches(8.9), Inches(3.1), Inches(3.6), data.active_employees, "Active Employees")
    _text(slide, Inches(0.8), Inches(6.5), Inches(11.7), Inches(0.4), f"Prepared By: Rotic HRM System · Generated {data.generated_at.strftime('%d %b %Y, %H:%M')}", size=12, color=ICE, align=PP_ALIGN.CENTER)

    # --- Slide 2: Executive Summary ---
    slide = _blank(prs)
    _title_bar(slide, _headline_sentence(data))
    _bar_chart(
        slide, Inches(0.6), Inches(1.55), Inches(7.2), Inches(3.2),
        "Weekly HR Performance: Week Before vs Current Week",
        [row.label for row in data.headline_comparison],
        [
            ("Week Before", [row.previous for row in data.headline_comparison]),
            ("Current Week", [row.current for row in data.headline_comparison]),
        ],
        palette=[SLATE, NAVY],
    )
    summary = (
        f"{_trend_phrase(present_row, 'Present attendance').capitalize()} records this week, against "
        f"{_fmt(present_row.previous)} the week before. "
        f"{_trend_phrase(hires_row, 'New hires').capitalize()}, "
        f"{_trend_phrase(leave_row, 'leave requests')}, and "
        f"{_trend_phrase(meals_row, 'meal tickets')}. "
        f"Active headcount stands at {data.active_employees}, versus an estimated {data.previous_active_employees} the week before."
    )
    _text(slide, Inches(0.6), Inches(4.95), Inches(7.2), Inches(1.9), "Executive Summary", size=14, bold=True, color=NAVY)
    _text(slide, Inches(0.6), Inches(5.35), Inches(7.2), Inches(1.6), summary, size=11.5, color=CHARCOAL, line_spacing=1.15)

    _growth_callout(slide, Inches(8.1), Inches(1.6), Inches(2.15), present_row)
    _growth_callout(slide, Inches(8.1), Inches(3.0), Inches(2.15), leave_row)
    _growth_callout(slide, Inches(8.1), Inches(4.4), Inches(2.15), hires_row)
    _point_callout(slide, Inches(10.5), Inches(1.6), Inches(2.15), "Attendance Rate", data.previous_attendance_rate, data.attendance_rate)
    _growth_callout(slide, Inches(10.5), Inches(3.0), Inches(2.15), absent_row)
    _growth_callout(slide, Inches(10.5), Inches(4.4), Inches(2.15), meals_row)
    _footer(slide, "Executive Summary")

    # --- Slide 3: Daily Breakdown ---
    slide = _blank(prs)
    peak_present = max(data.attendance_by_day, key=lambda d: d.present, default=None)
    peak_absent = max(data.attendance_by_day, key=lambda d: d.absent, default=None)
    if peak_present and peak_present.present > 0:
        headline3 = f"ATTENDANCE PEAKED ON {peak_present.date.strftime('%A').upper()} WITH {peak_present.present} PRESENT RECORDS"
    else:
        headline3 = "DAILY ATTENDANCE BREAKDOWN"
    _title_bar(slide, headline3)
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

    # --- Slide 4: Top Departments ---
    slide = _blank(prs)
    comparable = [row for row in data.department_comparison if row.previous > 0]
    leader = data.department_comparison[0] if data.department_comparison else None
    grower = max(comparable, key=lambda r: r.growth_pct, default=None)
    decliner = min(comparable, key=lambda r: r.growth_pct, default=None)
    headline4 = f"{leader.label.upper()} LED ATTENDANCE THIS WEEK WITH {_fmt(leader.current)} PRESENT RECORDS" if leader else "TOP DEPARTMENTS BY ATTENDANCE"
    _title_bar(slide, headline4)
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

    # --- Slide 5: Leave Type Breakdown ---
    slide = _blank(prs)
    top_leave = data.leave_type_comparison[0] if data.leave_type_comparison else None
    if top_leave and top_leave.current > 0:
        share = top_leave.current / max(data.leave_submitted, 1) * 100
        headline5 = f"{top_leave.label.upper()} REMAINS THE LEADING LEAVE TYPE AT {_fmt(top_leave.current)} REQUESTS, {_growth_label(top_leave.growth_pct)} WEEK-OVER-WEEK"
    else:
        headline5 = "LEAVE REQUESTS BY TYPE"
    _title_bar(slide, headline5)
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

    # --- Slide 6: New Hires & Exits Detail ---
    slide = _blank(prs)
    net_change = len(data.new_hires) - len(data.exits)
    affected_departments = {p.department for p in (data.new_hires + data.exits) if p.department and p.department != "-"}
    headline6 = f"{len(data.new_hires)} NEW HIRE{'S' if len(data.new_hires) != 1 else ''} AND {len(data.exits)} EXIT{'S' if len(data.exits) != 1 else ''} THIS WEEK — WORKFORCE MOVEMENT DETAIL"
    _title_bar(slide, headline6)
    _stat_row(slide, Inches(1.55), [
        (len(data.new_hires), "New Hires", GREEN),
        (len(data.exits), "Exits", RED),
        (("+" if net_change >= 0 else "") + str(net_change), "Net Change", NAVY),
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

    # --- Slide 7: Recommendations ---
    slide = _blank(prs)
    _title_bar(slide, "RECOMMENDATIONS")

    if data.top_absentee_departments:
        top_dept = data.top_absentee_departments[0]
        rec1 = f"{top_dept.name} recorded the most absences this week, with {top_dept.count}. Investigate attendance patterns there and confirm rosters are being followed."
    else:
        rec1 = "No department stood out for absences this week — attendance patterns look even across departments."

    if top_leave and top_leave.current > 0 and top_leave.growth_pct > 0:
        rec2 = f"Demand for {top_leave.label} grew {_growth_label(top_leave.growth_pct)} this week ({_fmt(top_leave.previous)} → {_fmt(top_leave.current)} requests). Plan cover for the departments most affected."
    elif top_leave and top_leave.current > 0:
        rec2 = f"{top_leave.label} remains the most requested leave type at {_fmt(top_leave.current)} requests, though demand has eased from the week before."
    else:
        rec2 = "Leave activity was quiet this week — no type stood out."

    if data.leave_pending > 0:
        rec3 = f"{data.leave_pending} leave request{'s' if data.leave_pending != 1 else ''} still await{'s' if data.leave_pending == 1 else ''} a decision. Clear the approval queue before it grows further."
    else:
        rec3 = "The leave approval queue is fully cleared — no pending requests carried over this week."

    top = Inches(1.7)
    for index, (heading, text) in enumerate([
        ("Attendance", rec1),
        ("Leave Demand", rec2),
        ("Approval Backlog", rec3),
    ]):
        _rect(slide, Inches(0.6), top, Inches(0.12), Inches(1.3), [NAVY, AMBER, GREEN][index])
        _text(slide, Inches(0.95), top, Inches(11.5), Inches(0.35), heading, size=15, bold=True, color=NAVY)
        _text(slide, Inches(0.95), top + Inches(0.4), Inches(11.5), Inches(0.85), text, size=12.5, color=CHARCOAL, line_spacing=1.2)
        top = top + Inches(1.65)
    _footer(slide, "Recommendations")

    output = io.BytesIO()
    prs.save(output)
    return output.getvalue()
