"""
Tiny shared helper for "Download CSV" buttons across the app (Customers,
Milk Entries, Buyer Invoices, ...). Uses only the standard library's `csv`
module - no pandas/openpyxl needed for CSV, and no third-party service.
"""
import csv
import io

from flask import Response


def csv_response(filename, header_row, data_rows):
    """
    Builds a downloadable CSV Flask response.

    header_row: list of column titles.
    data_rows: iterable of lists/tuples, one per row, same length/order as
        header_row.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header_row)
    writer.writerows(data_rows)

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
