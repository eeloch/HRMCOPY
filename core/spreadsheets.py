"""Helpers for writing untrusted text (names, staff numbers, plan names) into XLSX files.

openpyxl turns any string that starts with "=" into a formula cell, so a crafted staff number or name would run as a
formula when someone opens the exported workbook (2026-09-25 review, S-07). Everything user-controlled goes through
here so it stays literal text."""

FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def append_text_row(sheet, values):
    """sheet.append(values) but any string is stored as text, never as a formula."""
    sheet.append(list(values))
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str) and cell.value.startswith(FORMULA_TRIGGERS):
            cell.data_type = "s"
