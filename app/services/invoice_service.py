import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from app.utils.id_generator import get_next_sequence


def get_or_create_invoice_number(db, payment):
    """
    Assigns a permanent invoice number the FIRST time a payment's invoice
    is viewed, then reuses that same number on every later view/print/PDF.
    This means reprinting an invoice never changes its official number,
    which matters for the dairy's own bookkeeping.
    """
    if payment.get("invoice_number"):
        return payment["invoice_number"], payment["invoice_date"]

    seq = get_next_sequence(db, "invoice_number")
    invoice_number = f"INV{seq:05d}"
    invoice_date = datetime.now(timezone.utc)

    db.payments.update_one(
        {"_id": payment["_id"]},
        {"$set": {"invoice_number": invoice_number, "invoice_date": invoice_date}},
    )
    return invoice_number, invoice_date


def generate_invoice_pdf(dairy_info, payment, entries):
    """
    Builds a PDF invoice in memory and returns a BytesIO buffer ready to
    send to the browser. Uses "Rs" instead of the Rupee symbol because
    ReportLab's built-in fonts don't include that glyph.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("DairyTitle", parent=styles["Title"], fontSize=18, spaceAfter=2)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=9, textColor=colors.grey)
    heading = ParagraphStyle("SectionHeading", parent=styles["Heading3"], spaceBefore=8, spaceAfter=4)

    story = []

    # --- Dairy header ---
    story.append(Paragraph(dairy_info["name"], title_style))
    story.append(Paragraph(dairy_info["address"], small))
    story.append(Paragraph(f"Contact: {dairy_info['contact']}", small))
    story.append(Spacer(1, 8 * mm))

    # --- Invoice + customer meta, two columns ---
    meta_data = [
        ["Invoice Number:", payment["invoice_number"], "Customer ID:", payment["customer_id"]],
        [
            "Invoice Date:",
            payment["invoice_date"].strftime("%d %b %Y"),
            "Customer Name:",
            payment["customer_name"],
        ],
        [
            "Payment Period:",
            payment["payment_period"],
            "Mobile:",
            payment.get("customer_mobile", "-"),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[32 * mm, 55 * mm, 30 * mm, 55 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 6 * mm))

    # --- Milk collection table ---
    story.append(Paragraph("Milk Collection Details", heading))

    table_data = [["Date", "Shift", "Type", "Qty (L)", "Fat %", "SNF %", "Rate (Rs)", "Amount (Rs)"]]
    for e in entries:
        table_data.append(
            [
                e["date"].strftime("%d %b"),
                e["shift"],
                e["milk_type"],
                f"{e['quantity']:.2f}",
                f"{e['fat_percentage']}",
                f"{e['snf_percentage']}",
                f"{e['rate_per_litre']:.2f}",
                f"{e['total_amount']:.2f}",
            ]
        )

    entries_table = Table(
        table_data,
        colWidths=[20 * mm, 18 * mm, 16 * mm, 18 * mm, 15 * mm, 15 * mm, 22 * mm, 25 * mm],
        repeatRows=1,
    )
    entries_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#212529")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dee2e6")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(entries_table)
    story.append(Spacer(1, 6 * mm))

    # --- Summary ---
    summary_data = [
        ["Total Quantity", f"{payment['total_quantity']:.2f} L"],
        ["Gross Amount", f"Rs {payment['gross_amount']:.2f}"],
        ["Deductions", f"Rs {payment['deductions']:.2f}"],
        ["Previous Pending Amount", f"Rs {payment['previous_pending_amount']:.2f}"],
        ["Final Payable Amount", f"Rs {payment['final_payable_amount']:.2f}"],
        ["Amount Paid", f"Rs {payment['amount_paid']:.2f}"],
        ["Remaining Balance", f"Rs {payment['remaining_amount']:.2f}"],
        ["Payment Status", payment["payment_status"]],
    ]
    summary_table = Table(summary_data, colWidths=[80 * mm, 40 * mm], hAlign="RIGHT")
    summary_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, 4), (-1, 4), 0.75, colors.black),
                ("FONTNAME", (0, 4), (-1, 4), "Helvetica-Bold"),
                ("FONTNAME", (0, 6), (-1, 6), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph("Thank you for your business.", small))

    doc.build(story)
    buffer.seek(0)
    return buffer
