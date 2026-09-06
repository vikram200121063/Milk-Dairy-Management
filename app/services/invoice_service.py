import io
import os
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    HRFlowable,
    KeepTogether,
    Image,
)

from app.utils.id_generator import get_next_sequence

# ---------------------------------------------------------------------------
# Brand palette - kept in sync with app/static/css/style.css so the PDF
# invoice looks like it belongs to the same product as the web app.
# ---------------------------------------------------------------------------
CREAM = colors.HexColor("#FBF3E4")
CREAM_ALT = colors.HexColor("#F3E7CC")
FOREST_DARK = colors.HexColor("#143229")
MARIGOLD = colors.HexColor("#E2960F")
MARIGOLD_DARK = colors.HexColor("#C27E09")
INK = colors.HexColor("#241F16")
INK_MUTED = colors.HexColor("#6B6455")
BORDER = colors.HexColor("#E4D6B4")
SUCCESS = colors.HexColor("#2F7A4F")
SUCCESS_SOFT = colors.HexColor("#E4F1E8")
WARNING_SOFT = colors.HexColor("#FCF0DA")
DANGER = colors.HexColor("#B23B2E")
DANGER_SOFT = colors.HexColor("#F8E5E2")

# Directory holding the dairy's logo. To use your own logo instead of the
# built-in mark, just replace app/static/img/logo-mark.svg with your own
# file of the same name - or drop a logo.svg/logo.png/logo.jpg alongside
# it, since _find_logo_path() below checks a few common filenames.
LOGO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "img")
LOGO_CANDIDATES = (
    "logo-mark.svg", "logo.svg",
    "logo-mark.png", "logo.png",
    "logo-mark.jpg", "logo.jpg", "logo-mark.jpeg", "logo.jpeg",
)


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


def _find_logo_path():
    for name in LOGO_CANDIDATES:
        candidate = os.path.join(LOGO_DIR, name)
        if os.path.exists(candidate):
            return candidate
    return None


def _load_logo_flowable(target_height=13 * mm):
    """
    Loads the dairy's logo (SVG via svglib, or PNG/JPG directly) as a
    ReportLab flowable scaled to a fixed height, preserving its aspect
    ratio. Returns None - rather than raising - if no logo file is found
    or it can't be parsed, so a logo problem never breaks invoice
    generation; the header just falls back to text-only.
    """
    path = _find_logo_path()
    if not path:
        return None

    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".svg":
            from svglib.svglib import svg2rlg

            drawing = svg2rlg(path)
            if not drawing or not drawing.height:
                return None
            scale = target_height / drawing.height
            drawing.width *= scale
            drawing.height *= scale
            drawing.scale(scale, scale)
            return drawing

        from PIL import Image as PILImage

        with PILImage.open(path) as img:
            native_width, native_height = img.size
        if not native_height:
            return None
        scale = target_height / native_height
        return Image(path, width=native_width * scale, height=native_height * scale)
    except Exception:
        return None


def _status_colors(status):
    if status == "Paid":
        return SUCCESS, SUCCESS_SOFT
    if status == "Partially Paid":
        return MARIGOLD_DARK, WARNING_SOFT
    return DANGER, DANGER_SOFT


def _status_badge(status, styles):
    """A small pill-style flowable showing the payment status in color."""
    text_color, bg_color = _status_colors(status)
    style = ParagraphStyle(
        "StatusBadge",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        textColor=text_color,
        alignment=TA_CENTER,
    )
    badge = Table([[Paragraph(status.upper(), style)]], colWidths=[38 * mm])
    badge.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg_color),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.75, text_color),
            ]
        )
    )
    return badge


def generate_invoice_pdf(dairy_info, payment, entries):
    """
    Builds a branded PDF invoice in memory and returns a BytesIO buffer
    ready to send to the browser. Uses "Rs" instead of the Rupee symbol
    because ReportLab's built-in fonts don't include that glyph.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        title=f"Invoice {payment.get('invoice_number', '')}",
    )

    styles = getSampleStyleSheet()
    dairy_name_style = ParagraphStyle(
        "DairyName", parent=styles["Normal"], fontName="Times-Bold",
        fontSize=18, textColor=CREAM, leading=21,
    )
    dairy_meta_style = ParagraphStyle(
        "DairyMeta", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8.5, textColor=CREAM_ALT, leading=12,
    )
    invoice_title_style = ParagraphStyle(
        "InvoiceTitle", parent=styles["Normal"], fontName="Times-Bold",
        fontSize=20, textColor=MARIGOLD, alignment=TA_RIGHT, leading=22,
    )
    invoice_meta_style = ParagraphStyle(
        "InvoiceMeta", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9, textColor=CREAM_ALT, alignment=TA_RIGHT, leading=13,
    )
    section_label_style = ParagraphStyle(
        "SectionLabel", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=8, textColor=INK_MUTED, leading=11,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=11, textColor=INK, leading=14,
    )
    body_small_style = ParagraphStyle(
        "BodySmall", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9.5, textColor=INK_MUTED, leading=13,
    )
    heading_style = ParagraphStyle(
        "SectionHeading", parent=styles["Normal"], fontName="Times-Bold",
        fontSize=12.5, textColor=FOREST_DARK, spaceBefore=10, spaceAfter=6,
    )
    footer_style = ParagraphStyle(
        "Footer", parent=styles["Normal"], fontName="Helvetica-Oblique",
        fontSize=9, textColor=INK_MUTED, alignment=TA_CENTER,
    )
    footer_small_style = ParagraphStyle(
        "FooterSmall", parent=styles["Normal"], fontName="Helvetica",
        fontSize=7.5, textColor=INK_MUTED, alignment=TA_CENTER,
    )

    story = []

    # -----------------------------------------------------------------
    # Header band: dairy branding (left) + INVOICE title/meta (right),
    # on a solid forest-green background so it reads as a masthead.
    # -----------------------------------------------------------------
    logo = _load_logo_flowable()
    dairy_block = [
        Paragraph(dairy_info["name"], dairy_name_style),
        Paragraph(dairy_info["address"], dairy_meta_style),
        Paragraph(f"Contact: {dairy_info['contact']}", dairy_meta_style),
    ]
    if logo:
        left_cell = Table(
            [[logo, dairy_block]],
            colWidths=[16 * mm, 90 * mm],
        )
        left_cell.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
    else:
        left_cell = dairy_block

    right_block = [
        Paragraph("INVOICE", invoice_title_style),
        Paragraph(f"No. {payment['invoice_number']}", invoice_meta_style),
        Paragraph(payment["invoice_date"].strftime("%d %b %Y"), invoice_meta_style),
    ]

    header_table = Table([[left_cell, right_block]], colWidths=[112 * mm, 66 * mm])
    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), FOREST_DARK),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 12),
                ("RIGHTPADDING", (1, 0), (1, 0), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=2.5, color=MARIGOLD, spaceAfter=0))
    story.append(Spacer(1, 7 * mm))

    # -----------------------------------------------------------------
    # Bill-to / invoice-details / status strip
    # -----------------------------------------------------------------
    bill_to = [
        Paragraph("BILLED TO", section_label_style),
        Spacer(1, 2),
        Paragraph(payment["customer_name"], body_style),
        Paragraph(f"Customer ID: {payment['customer_id']}", body_small_style),
        Paragraph(f"Mobile: {payment.get('customer_mobile', '-')}", body_small_style),
    ]
    period_info = [
        Paragraph("BILLING PERIOD", section_label_style),
        Spacer(1, 2),
        Paragraph(payment["payment_period"], body_style),
        Paragraph(
            f"{payment['cycle_start'].strftime('%d %b')} \u2013 "
            f"{payment['cycle_end'].strftime('%d %b %Y')}",
            body_small_style,
        ),
    ]
    status_info = [
        Paragraph("STATUS", section_label_style),
        Spacer(1, 3),
        _status_badge(payment["payment_status"], styles),
    ]

    meta_strip = Table(
        [[bill_to, period_info, status_info]],
        colWidths=[70 * mm, 60 * mm, 48 * mm],
    )
    meta_strip.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), CREAM_ALT),
                ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
                ("LINEAFTER", (0, 0), (0, 0), 0.75, BORDER),
                ("LINEAFTER", (1, 0), (1, 0), 0.75, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(meta_strip)

    # -----------------------------------------------------------------
    # Milk collection table
    # -----------------------------------------------------------------
    story.append(Paragraph("Milk Collection Details", heading_style))

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
                ("BACKGROUND", (0, 0), (-1, 0), FOREST_DARK),
                ("TEXTCOLOR", (0, 0), (-1, 0), CREAM),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CREAM_ALT]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(entries_table)
    if not entries:
        story.append(Spacer(1, 2))
        story.append(Paragraph("No milk entries were recorded in this cycle.", body_small_style))
    story.append(Spacer(1, 7 * mm))

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    remaining_color = SUCCESS if payment["remaining_amount"] <= 0 else DANGER

    summary_rows = [
        ["Total Quantity", f"{payment['total_quantity']:.2f} L"],
        ["Gross Amount", f"Rs {payment['gross_amount']:.2f}"],
        ["Deductions", f"- Rs {payment['deductions']:.2f}"],
        ["Previous Pending Amount", f"+ Rs {payment['previous_pending_amount']:.2f}"],
        ["Final Payable Amount", f"Rs {payment['final_payable_amount']:.2f}"],
        ["Amount Paid", f"Rs {payment['amount_paid']:.2f}"],
        ["Remaining Balance", f"Rs {payment['remaining_amount']:.2f}"],
    ]
    FINAL_ROW = 4
    REMAINING_ROW = 6

    summary_table = Table(summary_rows, colWidths=[62 * mm, 42 * mm], hAlign="RIGHT")
    summary_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEABOVE", (0, FINAL_ROW), (-1, FINAL_ROW), 0.75, FOREST_DARK),
                ("BACKGROUND", (0, FINAL_ROW), (-1, FINAL_ROW), CREAM_ALT),
                ("FONTNAME", (0, FINAL_ROW), (-1, FINAL_ROW), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, FINAL_ROW), (-1, FINAL_ROW), FOREST_DARK),
                ("FONTNAME", (0, REMAINING_ROW), (-1, REMAINING_ROW), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, REMAINING_ROW), (-1, REMAINING_ROW), remaining_color),
                ("LINEBELOW", (0, REMAINING_ROW), (-1, REMAINING_ROW), 0.5, BORDER),
            ]
        )
    )
    # -----------------------------------------------------------------
    # Footer
    # -----------------------------------------------------------------
    footer_block = [
        Spacer(1, 4 * mm),
        HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=6),
        Paragraph(f"Thank you for choosing {dairy_info['name']}.", footer_style),
        Paragraph(
            f"This is a system-generated invoice \u00b7 Generated on "
            f"{datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M UTC')}",
            footer_small_style,
        ),
    ]

    # Keep the summary table intact (never split mid-row across a page
    # break) and glued to the footer that follows it.
    story.append(KeepTogether([summary_table] + footer_block))

    doc.build(story)
    buffer.seek(0)
    return buffer


# ---------------------------------------------------------------------------
# Buyer invoices (Buyer Management / Accounting module)
#
# A separate status-color helper is used here (rather than extending
# _status_colors/_status_badge above) so buyer invoice statuses - which
# include "Unpaid" and "Overdue", not used by customer payments - can
# never change how existing customer invoice PDFs render.
# ---------------------------------------------------------------------------


def _buyer_status_colors(status):
    if status == "Paid":
        return SUCCESS, SUCCESS_SOFT
    if status == "Partially Paid":
        return MARIGOLD_DARK, WARNING_SOFT
    if status == "Overdue":
        return DANGER, DANGER_SOFT
    return INK_MUTED, CREAM_ALT  # Unpaid, not yet due


def _buyer_status_badge(status, styles):
    text_color, bg_color = _buyer_status_colors(status)
    style = ParagraphStyle(
        "BuyerStatusBadge",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        textColor=text_color,
        alignment=TA_CENTER,
    )
    badge = Table([[Paragraph(status.upper(), style)]], colWidths=[38 * mm])
    badge.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg_color),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.75, text_color),
            ]
        )
    )
    return badge


def generate_buyer_invoice_pdf(dairy_info, invoice, buyer, sales):
    """
    Builds a branded PDF for one consolidated buyer invoice - the same
    visual language as the customer invoice above (logo, forest-green
    masthead, marigold accent) so both feel like they belong to the same
    product, but with buyer-specific fields (GST number, tax, late
    payment interest).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        title=f"Invoice {invoice.get('invoice_id', '')}",
    )

    styles = getSampleStyleSheet()
    dairy_name_style = ParagraphStyle(
        "BDairyName", parent=styles["Normal"], fontName="Times-Bold",
        fontSize=18, textColor=CREAM, leading=21,
    )
    dairy_meta_style = ParagraphStyle(
        "BDairyMeta", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8.5, textColor=CREAM_ALT, leading=12,
    )
    invoice_title_style = ParagraphStyle(
        "BInvoiceTitle", parent=styles["Normal"], fontName="Times-Bold",
        fontSize=20, textColor=MARIGOLD, alignment=TA_RIGHT, leading=22,
    )
    invoice_meta_style = ParagraphStyle(
        "BInvoiceMeta", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9, textColor=CREAM_ALT, alignment=TA_RIGHT, leading=13,
    )
    section_label_style = ParagraphStyle(
        "BSectionLabel", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=8, textColor=INK_MUTED, leading=11,
    )
    body_style = ParagraphStyle(
        "BBody", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=11, textColor=INK, leading=14,
    )
    body_small_style = ParagraphStyle(
        "BBodySmall", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9.5, textColor=INK_MUTED, leading=13,
    )
    heading_style = ParagraphStyle(
        "BSectionHeading", parent=styles["Normal"], fontName="Times-Bold",
        fontSize=12.5, textColor=FOREST_DARK, spaceBefore=10, spaceAfter=6,
    )
    footer_style = ParagraphStyle(
        "BFooter", parent=styles["Normal"], fontName="Helvetica-Oblique",
        fontSize=9, textColor=INK_MUTED, alignment=TA_CENTER,
    )
    footer_small_style = ParagraphStyle(
        "BFooterSmall", parent=styles["Normal"], fontName="Helvetica",
        fontSize=7.5, textColor=INK_MUTED, alignment=TA_CENTER,
    )

    story = []

    # -----------------------------------------------------------------
    # Header band
    # -----------------------------------------------------------------
    logo = _load_logo_flowable()
    dairy_block = [
        Paragraph(dairy_info["name"], dairy_name_style),
        Paragraph(dairy_info["address"], dairy_meta_style),
        Paragraph(f"Contact: {dairy_info['contact']}", dairy_meta_style),
    ]
    if logo:
        left_cell = Table([[logo, dairy_block]], colWidths=[16 * mm, 90 * mm])
        left_cell.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
    else:
        left_cell = dairy_block

    right_block = [
        Paragraph("TAX INVOICE" if invoice.get("tax_amount") else "INVOICE", invoice_title_style),
        Paragraph(f"No. {invoice['invoice_id']}", invoice_meta_style),
        Paragraph(invoice["invoice_date"].strftime("%d %b %Y"), invoice_meta_style),
    ]

    header_table = Table([[left_cell, right_block]], colWidths=[112 * mm, 66 * mm])
    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), FOREST_DARK),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 12),
                ("RIGHTPADDING", (1, 0), (1, 0), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=2.5, color=MARIGOLD, spaceAfter=0))
    story.append(Spacer(1, 7 * mm))

    # -----------------------------------------------------------------
    # Bill-to / invoice-details / status strip
    # -----------------------------------------------------------------
    bill_to = [
        Paragraph("BILLED TO", section_label_style),
        Spacer(1, 2),
        Paragraph(buyer["company_name"] if buyer else invoice["buyer_name"], body_style),
        Paragraph(f"Buyer ID: {invoice['buyer_id']}", body_small_style),
    ]
    if buyer and buyer.get("gst_number"):
        bill_to.append(Paragraph(f"GSTIN: {buyer['gst_number']}", body_small_style))
    if buyer and buyer.get("address"):
        bill_to.append(Paragraph(buyer["address"], body_small_style))

    period_info = [
        Paragraph("BILLING PERIOD", section_label_style),
        Spacer(1, 2),
        Paragraph(invoice["billing_period"], body_style),
        Paragraph(
            f"{invoice['period_start'].strftime('%d %b')} \u2013 "
            f"{invoice['period_end'].strftime('%d %b %Y')}",
            body_small_style,
        ),
        Paragraph(f"Due: {invoice['due_date'].strftime('%d %b %Y')}", body_small_style),
    ]
    status_info = [
        Paragraph("STATUS", section_label_style),
        Spacer(1, 3),
        _buyer_status_badge(invoice["status"], styles),
    ]

    meta_strip = Table([[bill_to, period_info, status_info]], colWidths=[70 * mm, 60 * mm, 48 * mm])
    meta_strip.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), CREAM_ALT),
                ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
                ("LINEAFTER", (0, 0), (0, 0), 0.75, BORDER),
                ("LINEAFTER", (1, 0), (1, 0), 0.75, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(meta_strip)

    # -----------------------------------------------------------------
    # Sales table
    # -----------------------------------------------------------------
    story.append(Paragraph("Milk Sale Details", heading_style))

    table_data = [["Date", "Type", "Qty (L)", "Rate (Rs)", "Gross (Rs)", "Tax (Rs)", "Other (Rs)", "Total (Rs)"]]
    for s in sales:
        table_data.append(
            [
                s["sale_date"].strftime("%d %b"),
                s["milk_type"],
                f"{s['quantity']:.2f}",
                f"{s['rate_per_litre']:.2f}",
                f"{s['gross_amount']:.2f}",
                f"{s['tax_amount']:.2f}",
                f"{s['other_charges']:.2f}",
                f"{s['total_amount']:.2f}",
            ]
        )

    sales_table = Table(
        table_data,
        colWidths=[18 * mm, 18 * mm, 18 * mm, 20 * mm, 22 * mm, 18 * mm, 18 * mm, 22 * mm],
        repeatRows=1,
    )
    sales_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), FOREST_DARK),
                ("TEXTCOLOR", (0, 0), (-1, 0), CREAM),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CREAM_ALT]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(sales_table)
    if not sales:
        story.append(Spacer(1, 2))
        story.append(Paragraph("No milk sales were recorded in this billing period.", body_small_style))
    story.append(Spacer(1, 7 * mm))

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    remaining_color = SUCCESS if invoice["remaining_amount"] <= 0 else DANGER
    net_interest = round(invoice.get("interest_charged", 0) - invoice.get("interest_waived", 0), 2)

    summary_rows = [
        ["Total Quantity", f"{invoice['total_quantity']:.2f} L"],
        ["Gross Amount", f"Rs {invoice['gross_amount']:.2f}"],
        ["Tax / GST", f"+ Rs {invoice['tax_amount']:.2f}"],
        ["Other Charges", f"+ Rs {invoice['other_charges']:.2f}"],
    ]
    if net_interest > 0:
        summary_rows.append(["Late Payment Interest", f"+ Rs {net_interest:.2f}"])
    summary_rows.append(["Total Amount", f"Rs {invoice['total_amount']:.2f}"])
    final_row = len(summary_rows) - 1
    summary_rows.append(["Amount Paid", f"Rs {invoice['amount_paid']:.2f}"])
    summary_rows.append(["Balance Due", f"Rs {invoice['remaining_amount']:.2f}"])
    remaining_row = len(summary_rows) - 1

    summary_table = Table(summary_rows, colWidths=[62 * mm, 42 * mm], hAlign="RIGHT")
    summary_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEABOVE", (0, final_row), (-1, final_row), 0.75, FOREST_DARK),
                ("BACKGROUND", (0, final_row), (-1, final_row), CREAM_ALT),
                ("FONTNAME", (0, final_row), (-1, final_row), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, final_row), (-1, final_row), FOREST_DARK),
                ("FONTNAME", (0, remaining_row), (-1, remaining_row), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, remaining_row), (-1, remaining_row), remaining_color),
                ("LINEBELOW", (0, remaining_row), (-1, remaining_row), 0.5, BORDER),
            ]
        )
    )

    footer_block = [
        Spacer(1, 4 * mm),
        HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=6),
        Paragraph(f"Thank you for your business with {dairy_info['name']}.", footer_style),
        Paragraph(
            f"This is a system-generated invoice \u00b7 Generated on "
            f"{datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M UTC')}",
            footer_small_style,
        ),
    ]

    story.append(KeepTogether([summary_table] + footer_block))

    doc.build(story)
    buffer.seek(0)
    return buffer
