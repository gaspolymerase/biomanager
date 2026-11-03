"""Sheets out of BioManager: rows as CSV that Excel opens as UTF-8, or as an
.xlsx workbook, with no cell a spreadsheet would take for a formula; and
the mouse sheet's own export.
"""
from __future__ import annotations

import csv
import io
import re


_PLAIN_NUMBER = re.compile(r"^-?\d+(?:[.,]\d+)?$")


XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sheet_safe(value):
    """A cell a spreadsheet shows as text rather than runs: someone's note
    that starts with = + - @ (=HYPERLINK(…), say) would otherwise be a
    formula when the export is opened in Excel. A plain number stays one."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") and not _PLAIN_NUMBER.match(value):
        return "'" + value
    return value


def csv_text(rows) -> str:
    """Rows as CSV that Excel opens as UTF-8 (a byte-order mark) with no
    cell taken for a formula (sheet_safe)."""
    output = io.StringIO()
    writer = csv.writer(output)
    for row in rows:
        writer.writerow([sheet_safe(v) for v in row])
    return "\ufeff" + output.getvalue()


def xlsx_bytes(sheets) -> bytes:
    """[(title, rows)] as an .xlsx workbook, header rows bold, every text
    cell kept as text (never a formula)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    book = Workbook()
    for i, (title, rows) in enumerate(sheets):
        sheet = book.active if i == 0 else book.create_sheet()
        sheet.title = title[:31]
        for row in rows:
            sheet.append(row)
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.data_type = "s"
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def export_mouse_rows(mouse_rows: list[dict[str, object]], export_format: str) -> tuple[str | bytes, str, str]:
    """The Mice sheet as CSV, or as an Excel workbook (.xlsx) that Import
    from Excel reads back."""
    table = [
        [
            "Mouse_ID",
            "Active",
            "Age_weeks",
            "Age_days",
            "Gender",
            "Transgene_1",
            "Transgene_2",
            "Transgene_3",
            "Transgene_4",
            "Cage_ID",
            "Rack",
            "Position",
            "Cage_Location",
            "Owner",
            "Litter_ID",
            "DOB",
            "Status",
            "Date_of_Death",
            "Note",
        ]
    ]
    for row in mouse_rows:
        table.append(
            [
                row["mouse_id"],
                "Yes" if row["active"] else "No",
                "" if row["age_weeks"] is None else row["age_weeks"],   # a mouse born today is 0, not blank
                "" if row["age_days"] is None else row["age_days"],
                row["gender"],
                row["transgene_1"],
                row["transgene_2"],
                row["transgene_3"],
                row["transgene_4"],
                row["cage_id"],
                row.get("cage_rack", ""),
                row.get("cage_position", ""),
                row["cage_location"],
                row["owner"],
                row["litter_id"],
                row["date_of_birth"],
                row["status"],
                row["date_of_death"],
                row["note"],
            ]
        )
    if export_format == "csv":
        return csv_text(table), "mice_export.csv", "text/csv"
    return xlsx_bytes([("Mice", table)]), "mice_export.xlsx", XLSX_MIMETYPE
