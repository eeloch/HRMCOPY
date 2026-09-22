"""Builds the Weekly HR Report slide deck (.pptx) from a WeeklyReportData. No file is written to disk -
callers get bytes back, to stream straight out of an API response."""

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
    _text(slide, Inches(0.6), Inches(0.35), Inches(11.5), Inches(0.6), title, size=28, bold=True, color=NAVY)
    if subtitle:
        _text(slide, Inches(0.6), Inches(0.92), Inches(11.5), Inches(0.4), subtitle, size=13, color=SLATE)


def _stat_card(slide, left, top, width, height, value, label, color=NAVY):
    _rect(slide, left, top, width, height, WHITE, line_color=RGBColor(0xE2, 0xE6, 0xF0))
    _text(slide, left + Inches(0.15), top + Inches(0.12), width - Inches(0.3), height - Inches(0.75), str(value), size=32, bold=True, color=color)
    _text(slide, left + Inches(0.15), top + height - Inches(0.5), width - Inches(0.3), Inches(0.4), label, size=11, color=SLATE)


def _stat_row(slide, top, items, card_w=Inches(1.95), gap=Inches(0.2), start_left=Inches(0.6), card_h=Inches(1.3)):
    left = start_left
    for value, label, color in items:
        _stat_card(slide, left, top, card_w, card_h, value, label, color)
        left = left + card_w + gap


def _bar_chart(slide, left, top, width, height, title, categories, series):
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
    palette = [NAVY, AMBER, RED, GREEN]
    for index, plot_series in enumerate(chart.plots[0].series):
        plot_series.format.fill.solid()
        plot_series.format.fill.fore_color.rgb = palette[index % len(palette)]
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


def _render_people_list(slide, left, people, heading, heading_color, empty_message, *, max_rows=10):
    """A capped name/id/department table so an unusually large week (many hires or exits at once) can never
    overflow the slide - the count in the heading always reflects everyone, even when the table is capped."""
    width = Inches(5.9)
    _text(slide, left, Inches(1.55), width, Inches(0.35), heading, size=16, bold=True, color=heading_color)
    if not people:
        _text(slide, left, Inches(2.1), width, Inches(0.4), empty_message, size=12, color=SLATE, italic=True)
        return
    shown = people[:max_rows]
    _table(
        slide, left, Inches(2.0), width, Inches(0.4 * (len(shown) + 1)),
        ["Staff No.", "Name", "Department"],
        [(p.employee_id, p.name, p.department) for p in shown],
        col_widths=[Inches(1.2), Inches(3.0), Inches(1.7)],
    )
    if len(people) > max_rows:
        _text(slide, left, Inches(2.0) + Inches(0.4 * (len(shown) + 1)) + Inches(0.05), width, Inches(0.3),
              f"+ {len(people) - max_rows} more – see the full list in the app.", size=10, color=SLATE, italic=True)


def build_weekly_report_pptx(data, *, company_name="Rotic Aluminium"):
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    date_range = f"{data.week_start.strftime('%d %b')} - {data.week_end.strftime('%d %b %Y')}"

    # --- Slide 1: Title ---
    slide = _blank(prs)
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, NAVY)
    _text(slide, Inches(1), Inches(2.6), Inches(11.3), Inches(0.5), company_name.upper(), size=16, bold=True, color=ICE)
    _text(slide, Inches(1), Inches(3.05), Inches(11.3), Inches(1.2), "Weekly HR Report", size=44, bold=True, color=WHITE)
    _text(slide, Inches(1), Inches(4.0), Inches(11.3), Inches(0.5), f"Week {data.week_number} · {date_range}", size=18, color=ICE)
    _text(slide, Inches(1), Inches(6.7), Inches(11.3), Inches(0.4), f"Generated {data.generated_at.strftime('%d %b %Y, %H:%M')} · Rotic HRM System", size=11, color=RGBColor(0x9A, 0xAE, 0xE0))

    # --- Slide 2: Workforce Overview ---
    slide = _blank(prs)
    _title_bar(slide, "Workforce Overview", "Headcount as of the end of the week")
    _stat_row(slide, Inches(1.55), [
        (data.total_employees, "Total Employees", NAVY),
        (data.active_employees, "Active", GREEN),
        (data.inactive_employees, "Inactive", SLATE),
        (len(data.new_hires), "New Hires This Week", GREEN),
        (len(data.exits), "Exits This Week", RED),
    ])
    if data.by_department:
        _bar_chart(
            slide, Inches(0.6), Inches(3.1), Inches(7.4), Inches(3.7),
            "Active Employees by Department",
            [row.name for row in data.by_department],
            [("Employees", [row.count for row in data.by_department])],
        )
    if data.by_employment_type:
        _table(
            slide, Inches(8.3), Inches(3.1), Inches(4.4), Inches(0.4 * (len(data.by_employment_type) + 1)),
            ["Employment Type", "Count"],
            [(row.name, row.count) for row in data.by_employment_type],
            col_widths=[Inches(3.0), Inches(1.4)],
        )
    _footer(slide, "Workforce")

    # --- Slide 3: New Hires & Exits ---
    slide = _blank(prs)
    _title_bar(slide, "New Hires & Exits", f"Movements during {date_range}")
    _render_people_list(slide, Inches(0.6), data.new_hires, f"New Hires ({len(data.new_hires)})", GREEN, "No new hires this week.")
    _render_people_list(slide, Inches(6.8), data.exits, f"Exits ({len(data.exits)})", RED, "No exits this week.")
    _footer(slide, "Workforce")

    # --- Slide 4: Attendance ---
    slide = _blank(prs)
    _title_bar(slide, "Attendance This Week", f"Monday - Saturday, {date_range}")
    _stat_row(slide, Inches(1.55), [
        (data.attendance_present, "Present", GREEN),
        (data.attendance_late, "Late", AMBER),
        (data.attendance_absent, "Absent", RED),
        (data.attendance_on_leave, "On Leave", NAVY),
        (data.night_shift_records, "Night Shift", SLATE),
    ])
    if data.attendance_by_day:
        _bar_chart(
            slide, Inches(0.6), Inches(3.1), Inches(8.0), Inches(3.7),
            "Present / Late / Absent by Day",
            [row.date.strftime("%a %d") for row in data.attendance_by_day],
            [
                ("Present", [row.present for row in data.attendance_by_day]),
                ("Late", [row.late for row in data.attendance_by_day]),
                ("Absent", [row.absent for row in data.attendance_by_day]),
            ],
        )
    if data.top_absentee_departments:
        _text(slide, Inches(8.9), Inches(3.1), Inches(3.8), Inches(0.35), "Most Absences By Department", size=13, bold=True, color=CHARCOAL)
        _table(
            slide, Inches(8.9), Inches(3.5), Inches(3.8), Inches(0.4 * (len(data.top_absentee_departments) + 1)),
            ["Department", "Absences"],
            [(row.name, row.count) for row in data.top_absentee_departments],
            col_widths=[Inches(2.6), Inches(1.2)],
        )
    _footer(slide, "Attendance")

    # --- Slide 5: Leave ---
    slide = _blank(prs)
    _title_bar(slide, "Leave This Week", f"Requests submitted or decided during {date_range}")
    _stat_row(slide, Inches(1.55), [
        (data.leave_submitted, "Submitted", NAVY),
        (data.leave_approved, "Approved", GREEN),
        (data.leave_pending, "Pending", AMBER),
        (data.leave_rejected, "Rejected", RED),
        (f"{data.leave_days_approved:g}", "Days Approved", SLATE),
    ])
    if data.leave_by_type:
        _table(
            slide, Inches(0.6), Inches(3.1), Inches(6.0), Inches(0.4 * (len(data.leave_by_type) + 1)),
            ["Leave Type", "Requests This Week"],
            [(row.name, row.count) for row in data.leave_by_type],
            col_widths=[Inches(3.8), Inches(2.2)],
        )
    else:
        _text(slide, Inches(0.6), Inches(3.1), Inches(6), Inches(0.4), "No leave requests were submitted this week.", size=12, color=SLATE, italic=True)
    _footer(slide, "Leave")

    # --- Slide 6: Meals ---
    slide = _blank(prs)
    _title_bar(slide, "Meal Tickets This Week", date_range)
    _stat_row(slide, Inches(1.55), [
        (data.meal_collections, "Total Tickets", NAVY),
        (data.meal_within_entitlement, "Within Entitlement", GREEN),
        (data.meal_excess, "Excess (Needs Review)", AMBER),
    ], card_w=Inches(2.6))
    _footer(slide, "Meals")

    output = io.BytesIO()
    prs.save(output)
    return output.getvalue()
