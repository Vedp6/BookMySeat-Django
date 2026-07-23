import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

logger = logging.getLogger(__name__)


def _do_send_ticket_email(order_id):
   
    from .models import Order
    from .ticket import generate_ticket_pdf

    try:
        order = Order.objects.select_related(
            "user", "schedule", "schedule__movie", "schedule__theater"
        ).get(id=order_id)
    except Order.DoesNotExist:
        logger.error("Ticket email: Order %s does not exist, not retrying.", order_id)
        return "order_not_found"

    if order.status != "paid":
        logger.warning(
            "Ticket email: Order %s is not paid (status=%s), skipping email.",
            order_id, order.status,
        )
        return "order_not_paid"

    if not order.user.email:
        logger.warning("Ticket email: Order %s user has no email on file.", order_id)
        return "no_email_address"

    pdf_bytes = generate_ticket_pdf(order)

    movie_name = order.schedule.movie.name
    show_time = order.schedule.show_time.strftime("%d %b %Y, %I:%M %p")

    email = EmailMessage(
        subject=f"Your BookMySeat ticket for {movie_name}",
        body=(
            f"Hi {order.user.username},\n\n"
            f"Your booking is confirmed! Your ticket for {movie_name} "
            f"({show_time}) is attached as a PDF.\n\n"
            f"Booking ID: BMS-{order.id:06d}\n"
            f"Theater: {order.schedule.theater.name} - {order.schedule.screen}\n\n"
            f"Show this PDF (or its QR code) at the theater entrance.\n\n"
            f"Enjoy the movie!\nBookMySeat"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[order.user.email],
    )
    email.attach(f"BMS-{order.id:06d}-ticket.pdf", pdf_bytes, "application/pdf")
    email.send(fail_silently=False)  # raises on failure - intentional, see docstring above

    order.ticket_emailed_at = timezone.now()
    order.save(update_fields=["ticket_emailed_at"])

    logger.info("Ticket emailed successfully for order %s to %s", order_id, order.user.email)
    return "sent"


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,       # 30s, 60s, 120s, 240s... exponential backoff
    retry_backoff_max=600,  # cap backoff at 10 minutes between attempts
    retry_jitter=True,      # avoid a thundering herd if many emails fail at once
    max_retries=5,
)
def send_ticket_email_task(self, order_id):
    """
    Celery task wrapper around _do_send_ticket_email(). Used when a
    Celery worker + broker (e.g. Redis) is actually available - see
    background_tasks.py for what runs instead when it isn't.
    """
    return _do_send_ticket_email(order_id)
