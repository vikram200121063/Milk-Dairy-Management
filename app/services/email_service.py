"""
Email notifications via plain SMTP - works with Gmail or any other mail
provider. For Gmail specifically you need an "App Password" (Google
Account -> Security -> 2-Step Verification -> App Passwords); a normal
account password will be rejected.
"""
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app


class EmailSendError(Exception):
    """Raised when the SMTP send fails, or email isn't configured."""


def email_configured():
    cfg = current_app.config
    return bool(cfg.get("SMTP_HOST") and cfg.get("SMTP_USERNAME") and cfg.get("SMTP_PASSWORD"))


def send_email(to_email, subject, html_body, attachment=None, attachment_filename=None):
    """
    Sends one HTML email, optionally with a single binary attachment
    (e.g. a PDF invoice) supplied as raw bytes.
    """
    cfg = current_app.config
    if not email_configured():
        raise EmailSendError(
            "Email is not configured. Set SMTP_HOST, SMTP_USERNAME, and "
            "SMTP_PASSWORD in your .env file."
        )

    from_name = cfg.get("SMTP_FROM_NAME") or cfg.get("DAIRY_NAME", "Dairy")
    from_email = cfg.get("SMTP_FROM_EMAIL") or cfg.get("SMTP_USERNAME")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    if attachment:
        filename = attachment_filename or "attachment.pdf"
        part = MIMEApplication(attachment, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)

    host = cfg["SMTP_HOST"]
    port = int(cfg.get("SMTP_PORT", 587))
    use_tls = str(cfg.get("SMTP_USE_TLS", "True")).lower() in ("true", "1")

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            if use_tls:
                server.starttls()
            server.login(cfg["SMTP_USERNAME"], cfg["SMTP_PASSWORD"])
            server.sendmail(from_email, [to_email], msg.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailSendError(f"Could not send email: {exc}") from exc


def send_milk_entry_email(customer, entry):
    """Sends the 'milk collection recorded' email for one entry."""
    subject = f"Milk Collection Recorded \u2013 {entry['date'].strftime('%d %b %Y')} ({entry['shift']})"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: auto;">
      <h2 style="color:#1F4A3A;">Milk Collection Recorded</h2>
      <p>Dear {customer['name']},</p>
      <p>We've recorded your milk delivery:</p>
      <table style="width:100%; border-collapse: collapse; font-size:14px;">
        <tr><td style="padding:4px 0; color:#666;">Date</td><td style="padding:4px 0; text-align:right;"><strong>{entry['date'].strftime('%d %b %Y')}</strong></td></tr>
        <tr><td style="padding:4px 0; color:#666;">Shift</td><td style="padding:4px 0; text-align:right;"><strong>{entry['shift']}</strong></td></tr>
        <tr><td style="padding:4px 0; color:#666;">Milk Type</td><td style="padding:4px 0; text-align:right;">{entry['milk_type']}</td></tr>
        <tr><td style="padding:4px 0; color:#666;">Quantity</td><td style="padding:4px 0; text-align:right;">{entry['quantity']:.2f} L</td></tr>
        <tr><td style="padding:4px 0; color:#666;">Fat %</td><td style="padding:4px 0; text-align:right;">{entry['fat_percentage']}</td></tr>
        <tr><td style="padding:4px 0; color:#666;">SNF %</td><td style="padding:4px 0; text-align:right;">{entry['snf_percentage']}</td></tr>
        <tr><td style="padding:4px 0; color:#666;">Rate</td><td style="padding:4px 0; text-align:right;">\u20b9{entry['rate_per_litre']:.2f}/L</td></tr>
        <tr><td style="padding:8px 0; border-top:1px solid #ddd;"><strong>Amount</strong></td><td style="padding:8px 0; border-top:1px solid #ddd; text-align:right;"><strong>\u20b9{entry['total_amount']:.2f}</strong></td></tr>
      </table>
      <p style="color:#999; font-size:12px; margin-top:24px;">
        This is an automated message. Please contact the dairy office for any queries.
      </p>
    </div>
    """
    send_email(customer["email"], subject, html_body)


def send_invoice_email(customer, payment, entries, dairy_info):
    """
    Sends the invoice email for one payment record, with the PDF invoice
    attached. Assumes `payment` already has invoice_number/invoice_date
    set (call invoice_service.get_or_create_invoice_number first).
    """
    from app.services import invoice_service  # local import avoids a cycle

    pdf_buffer = invoice_service.generate_invoice_pdf(dairy_info, payment, entries)

    subject = f"Invoice {payment['invoice_number']} \u2013 {payment['payment_period']}"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: auto;">
      <h2 style="color:#1F4A3A;">Your Invoice is Ready</h2>
      <p>Dear {customer['name']},</p>
      <p>
        Your invoice for the <strong>{payment['payment_period']}</strong> cycle
        ({payment['cycle_start'].strftime('%d %b')} &ndash; {payment['cycle_end'].strftime('%d %b %Y')})
        is attached as a PDF.
      </p>
      <table style="width:100%; border-collapse: collapse; font-size:14px;">
        <tr><td style="padding:4px 0; color:#666;">Total Quantity</td><td style="padding:4px 0; text-align:right;">{payment['total_quantity']:.2f} L</td></tr>
        <tr><td style="padding:4px 0; color:#666;">Gross Amount</td><td style="padding:4px 0; text-align:right;">\u20b9{payment['gross_amount']:.2f}</td></tr>
        <tr><td style="padding:4px 0; color:#666;">Deductions</td><td style="padding:4px 0; text-align:right;">- \u20b9{payment['deductions']:.2f}</td></tr>
        <tr><td style="padding:4px 0; color:#666;">Previous Pending</td><td style="padding:4px 0; text-align:right;">+ \u20b9{payment['previous_pending_amount']:.2f}</td></tr>
        <tr><td style="padding:8px 0; border-top:1px solid #ddd;"><strong>Final Payable</strong></td><td style="padding:8px 0; border-top:1px solid #ddd; text-align:right;"><strong>\u20b9{payment['final_payable_amount']:.2f}</strong></td></tr>
        <tr><td style="padding:4px 0; color:#666;">Amount Paid</td><td style="padding:4px 0; text-align:right;">\u20b9{payment['amount_paid']:.2f}</td></tr>
        <tr><td style="padding:4px 0;"><strong>Remaining Balance</strong></td><td style="padding:4px 0; text-align:right;"><strong>\u20b9{payment['remaining_amount']:.2f}</strong></td></tr>
      </table>
      <p style="color:#999; font-size:12px; margin-top:24px;">
        This is an automated message. Please contact the dairy office for any queries.
      </p>
    </div>
    """
    send_email(
        customer["email"],
        subject,
        html_body,
        attachment=pdf_buffer.getvalue(),
        attachment_filename=f"invoice_{payment['invoice_number']}.pdf",
    )
