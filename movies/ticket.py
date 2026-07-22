"""
Generates the PDF movie ticket for a paid Order.

Used from two places:
- movies/tasks.py -> the async Celery task that emails it after payment
- movies/views.py -> the synchronous "download ticket" view

Both call generate_ticket_pdf(order) and get identical bytes back, so the
emailed ticket and the one a user re-downloads later are always the same.
"""

import io

import qrcode
from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable,
)


def _build_verification_url(order):
    return f"{settings.SITE_URL}/movies/verify-ticket/{order.id}/{order.verification_token}/"


def _generate_qr_image(data):
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_ticket_pdf(order):
    """Returns the ticket PDF as raw bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A5,
        topMargin=14 * mm, bottomMargin=14 * mm,
        leftMargin=12 * mm, rightMargin=12 * mm,
    )

    styles = getSampleStyleSheet()
    brand_style = ParagraphStyle(
        "Brand", parent=styles["Heading1"], fontSize=18, textColor=colors.HexColor("#28a745"),
    )
    movie_style = ParagraphStyle(
        "MovieTitle", parent=styles["Heading2"], fontSize=15, spaceAfter=2,
    )
    label_style = ParagraphStyle(
        "Label", parent=styles["Normal"], fontSize=8, textColor=colors.grey,
    )
    value_style = ParagraphStyle(
        "Value", parent=styles["Normal"], fontSize=11, spaceAfter=6,
    )
    small_style = ParagraphStyle(
        "Small", parent=styles["Normal"], fontSize=8, textColor=colors.grey,
    )

    story = []

    story.append(Paragraph("BookMySeat", brand_style))
    story.append(Paragraph("E-TICKET", small_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#28a745"), thickness=1.2))
    story.append(Spacer(1, 10))

    schedule = order.schedule
    movie = schedule.movie
    theater = schedule.theater

    story.append(Paragraph(movie.name, movie_style))
    story.append(Paragraph(f"{movie.certificate} &bull; {movie.duration} min", small_style))
    story.append(Spacer(1, 10))

    seat_numbers = ", ".join(
        order.seats.order_by("seat_number").values_list("seat_number", flat=True)
    )

    payment = order.payments.filter(status="success").order_by("-verified_at").first()
    payment_ref = payment.razorpay_payment_id if payment else "N/A"

    details = [
        ("Theater", theater.name),
        ("Screen", schedule.screen),
        ("Location", f"{theater.location}, {theater.city}" if theater.city else theater.location),
        ("Show Time", schedule.show_time.strftime("%A, %d %B %Y - %I:%M %p")),
        ("Seats", seat_numbers or "-"),
        ("Booking ID", f"BMS-{order.id:06d}"),
        ("Payment Reference", payment_ref),
        ("Amount Paid", f"Rs. {order.amount}"),
        ("Booked By", order.user.get_full_name() or order.user.username),
    ]

    rows = []
    for label, value in details:
        rows.append([
            Paragraph(label, label_style),
            Paragraph(str(value), value_style),
        ])

    detail_table = Table(rows, colWidths=[35 * mm, 90 * mm])
    detail_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(detail_table)

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#dddddd"), thickness=0.8))
    story.append(Spacer(1, 10))

    qr_buf = _generate_qr_image(_build_verification_url(order))
    qr_img = Image(qr_buf, width=32 * mm, height=32 * mm)

    qr_table = Table(
        [[qr_img, Paragraph(
            "Scan at the theater entrance to verify this ticket. "
            "This QR code is unique to your booking and cannot be reused "
            "for a different order.",
            small_style,
        )]],
        colWidths=[36 * mm, 89 * mm],
    )
    qr_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(qr_table)

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "Please arrive at least 15 minutes before showtime. This ticket is valid "
        "only for the seats, show, and date listed above.",
        small_style,
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()
