from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q, Count
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from datetime import timedelta
import json
import logging
import uuid

from .models import (
    Movie,
    ShowSchedule,
    Seat,
    SeatReservation,
    Booking,
    Order,
    Payment,
    Refund,
    Review,
    ReviewReport,
)
from .forms import ReviewForm, ReviewReportForm
from . import payments as razorpay_helpers
from . import discovery
from .tasks import send_ticket_email_task
from . import background_tasks

logger = logging.getLogger(__name__)


def movie_list(request):
    qs = discovery.build_movie_queryset(request.GET)

    paginator, page = discovery.paginate(qs, request.GET.get("page"))
    total_count = paginator.count  # Paginator.count caches its own COUNT query - reuse it instead of calling qs.count() separately

    context = {
        "movies": page,
        "paginator": paginator,
        "total_count": total_count,
        "filter_choices": discovery.get_filter_choices(),
        "current": request.GET,
    }

    # AJAX requests (filter changes) get just the results partial re-rendered -
    # the filter form and "Recommended for You" section stay in place, so
    # only the grid/count/pagination actually update.
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "movies/_movie_grid.html", context)

    if request.user.is_authenticated:
        context["recommended_movies"] = discovery.recommended_for_user(request.user)
    else:
        context["recommended_movies"] = discovery.trending_fallback(10)

    return render(request, "movies/movie_list.html", context)


def movie_detail(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)

    discovery.track_recently_viewed(request.user, movie)

    reviews = movie.reviews.select_related("user").order_by("-created_at")

    review_form = None
    can_review = False
    user_review = None

    if request.user.is_authenticated:
        user_review = reviews.filter(user=request.user).first()

        # A booking counts as "watched" once its showtime has passed, so
        # reviewing unlocks automatically after the movie actually played -
        # no manual admin step needed for the common case. An admin can
        # still mark a booking watched early (e.g. after a preview
        # screening) via the "Mark selected bookings as watched" action.
        Booking.objects.filter(
            user=request.user,
            movie=movie,
            watched=False,
            schedule__show_time__lte=timezone.now(),
        ).update(watched=True)

        can_review = Booking.objects.filter(
            user=request.user, movie=movie, watched=True
        ).exists()

        if request.method == "POST" and "submit_review" in request.POST:
            if not can_review:
                messages.error(
                    request,
                    "You can only review a movie after you have booked "
                    "and watched it.",
                )
                return redirect("movie_detail", movie_id=movie.id)

            review_form = ReviewForm(request.POST, instance=user_review)
            if review_form.is_valid():
                review = review_form.save(commit=False)
                review.movie = movie
                review.user = request.user
                review.save()
                messages.success(request, "Your review has been saved.")
                return redirect("movie_detail", movie_id=movie.id)
        else:
            review_form = ReviewForm(instance=user_review)

    # Similar movies: same genre or language, excluding this one
    similar_movies = (
        Movie.objects.filter(Q(genre=movie.genre) | Q(language=movie.language))
        .exclude(id=movie.id)
        .distinct()[:8]
    )

    # Trending: most reviewed in the last 30 days
    trending_movies = (
        Movie.objects.exclude(id=movie.id)
        .annotate(
            recent_review_count=Count(
                "reviews",
                filter=Q(reviews__created_at__gte=timezone.now() - timedelta(days=30)),
            )
        )
        .order_by("-recent_review_count", "-average_rating")[:8]
    )

    # Recently released
    recent_movies = (
        Movie.objects.exclude(id=movie.id).order_by("-release_date")[:8]
    )

    # Star-rating distribution for the review summary bars, e.g.
    # {5: 12, 4: 3, 3: 1, 2: 0, 1: 0} - one GROUP BY query, not a Python
    # loop over every review.
    rating_counts = dict(
        reviews.values_list("rating").annotate(count=Count("id"))
    )
    total_reviews = sum(rating_counts.values())
    rating_breakdown = []
    for star in range(10, 0, -1):
        count = rating_counts.get(star, 0)
        pct = round((count / total_reviews) * 100, 1) if total_reviews else 0
        rating_breakdown.append({"star": star, "count": count, "pct": pct})

    return render(
        request,
        "movies/movie_detail.html",
        {
            "movie": movie,
            "reviews": reviews,
            "review_form": review_form,
            "can_review": can_review,
            "user_review": user_review,
            "similar_movies": similar_movies,
            "trending_movies": trending_movies,
            "recent_movies": recent_movies,
            "rating_breakdown": rating_breakdown,
            "total_reviews": total_reviews,
        },
    )


@login_required(login_url="/login/")
def edit_review(request, review_id):
    review = get_object_or_404(Review, id=review_id, user=request.user)

    if request.method == "POST":
        form = ReviewForm(request.POST, instance=review)
        if form.is_valid():
            form.save()
            messages.success(request, "Your review has been updated.")
            return redirect("movie_detail", movie_id=review.movie.id)
    else:
        form = ReviewForm(instance=review)

    return render(
        request,
        "movies/edit_review.html",
        {"form": form, "review": review},
    )


@login_required(login_url="/login/")
def delete_review(request, review_id):
    review = get_object_or_404(Review, id=review_id, user=request.user)
    movie_id = review.movie.id

    if request.method == "POST":
        review.delete()
        messages.success(request, "Your review has been deleted.")

    return redirect("movie_detail", movie_id=movie_id)


@login_required(login_url="/login/")
def report_review(request, review_id):
    review = get_object_or_404(Review, id=review_id)

    if review.user == request.user:
        messages.error(request, "You can't report your own review.")
        return redirect("movie_detail", movie_id=review.movie.id)

    if ReviewReport.objects.filter(review=review, reported_by=request.user).exists():
        messages.info(request, "You've already reported this review.")
        return redirect("movie_detail", movie_id=review.movie.id)

    if request.method == "POST":
        form = ReviewReportForm(request.POST)
        if form.is_valid():
            report = form.save(commit=False)
            report.review = review
            report.reported_by = request.user
            report.save()
            messages.success(request, "Thanks — this review has been reported for moderation.")
            return redirect("movie_detail", movie_id=review.movie.id)
    else:
        form = ReviewReportForm()

    return render(
        request,
        "movies/report_review.html",
        {"form": form, "review": review},
    )


def theater_list(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    schedules = (
        ShowSchedule.objects.filter(movie=movie)
        .select_related("theater")
        .order_by("theater__name", "show_time")
    )

    # Group show times by theater for display
    theaters = {}
    for schedule in schedules:
        theaters.setdefault(schedule.theater, []).append(schedule)

    return render(
        request,
        "movies/theater_list.html",
        {
            "movie": movie,
            "theaters": theaters.items(),
        },
    )


@login_required(login_url="/login/")
def book_seats(request, schedule_id):
    """
    Renders the seat map. Actual reservation and booking happen through the
    reserve_seat, seat_status, and payment_confirm endpoints below via AJAX,
    so this view no longer does the booking itself.
    """
    schedule = get_object_or_404(ShowSchedule, id=schedule_id)

    SeatReservation.clear_expired(schedule=schedule)

    seats = (
        Seat.objects.filter(schedule=schedule)
        .select_related("active_reservation", "active_reservation__user")
        .order_by("seat_number")
    )

    return render(
        request,
        "movies/seat_selection.html",
        {
            "schedule": schedule,
            "seats": seats,
            "hold_seconds": SeatReservation.HOLD_DURATION_SECONDS,
        },
    )


def _seat_status_payload(schedule, user):
    """Builds the JSON-serialisable live status of every seat in a show."""
    SeatReservation.clear_expired(schedule=schedule)

    seats = (
        Seat.objects.filter(schedule=schedule)
        .select_related("active_reservation", "active_reservation__user")
        .order_by("seat_number")
    )

    payload = []
    for seat in seats:
        status = seat.get_status(user)
        entry = {
            "id": seat.id,
            "seat_number": seat.seat_number,
            "status": status,
        }
        if status == "reserved_by_you":
            entry["seconds_remaining"] = seat.active_reservation.seconds_remaining()
        payload.append(entry)

    return payload


@login_required(login_url="/login/")
def seat_status(request, schedule_id):
    """
    Polled by the seat-selection page every few seconds so seats reserved
    or booked by other users show up as unavailable in near-real-time
    without needing websockets/Celery.
    """
    schedule = get_object_or_404(ShowSchedule, id=schedule_id)
    return JsonResponse({"seats": _seat_status_payload(schedule, request.user)})


@login_required(login_url="/login/")
@require_POST
def reserve_seat(request, schedule_id):
    """
    Toggles a temporary hold on a single seat for the current user.

    Wrapped in transaction.atomic() with select_for_update() so that when
    two users click the same seat at the same moment, the database serialises
    the two requests: whichever transaction commits first wins the seat, and
    the second is safely rejected instead of both succeeding.
    """
    schedule = get_object_or_404(ShowSchedule, id=schedule_id)
    seat_id = request.POST.get("seat_id")
    action = request.POST.get("action", "reserve")

    if not seat_id:
        return JsonResponse({"success": False, "error": "No seat specified."}, status=400)

    SeatReservation.clear_expired(schedule=schedule)

    try:
        with transaction.atomic():
            seat = Seat.objects.select_for_update().get(
                id=seat_id, schedule=schedule
            )

            if seat.is_booked:
                return JsonResponse(
                    {"success": False, "error": "This seat is already booked.", "status": "booked"},
                    status=409,
                )

            existing = SeatReservation.objects.select_for_update().filter(seat=seat).first()

            if action == "release":
                if existing and existing.user_id == request.user.id:
                    existing.delete()
                return JsonResponse({"success": True, "status": "available"})

            # action == "reserve"
            if existing:
                if existing.user_id == request.user.id:
                    # Already held by this user - just refresh the countdown.
                    existing.expires_at = timezone.now() + timedelta(
                        seconds=SeatReservation.HOLD_DURATION_SECONDS
                    )
                    existing.save(update_fields=["expires_at"])
                    return JsonResponse(
                        {
                            "success": True,
                            "status": "reserved_by_you",
                            "seconds_remaining": existing.seconds_remaining(),
                        }
                    )
                return JsonResponse(
                    {"success": False, "error": "Someone else just grabbed this seat.", "status": "reserved"},
                    status=409,
                )

            reservation = SeatReservation.objects.create(
                seat=seat, user=request.user, schedule=schedule
            )
            return JsonResponse(
                {
                    "success": True,
                    "status": "reserved_by_you",
                    "seconds_remaining": reservation.seconds_remaining(),
                }
            )

    except Seat.DoesNotExist:
        return JsonResponse({"success": False, "error": "Seat not found."}, status=404)


def _confirm_payment_success(order_id, razorpay_payment_id):
    """
    The single source of truth for turning a successful payment into real
    Bookings. Called from BOTH the client-side verification endpoint (fast
    UX) and the server-side webhook (authoritative, works even if the
    browser never calls back) - so it must be, and is, fully idempotent:
    calling it twice for the same payment must never create a second set
    of Bookings.

    Returns (ok: bool, reason: str).
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)

        if order.status == "paid":
            # Already processed by the other path (webhook vs client
            # callback racing each other) - idempotent no-op.
            return True, "already_paid"

        # The DB-level unique constraint on razorpay_payment_id is the
        # final safety net: if two requests for the same payment somehow
        # both reach here, only one get_or_create() wins the insert.
        payment, created = Payment.objects.get_or_create(
            razorpay_payment_id=razorpay_payment_id,
            defaults={
                "order": order,
                "status": "success",
                "amount": order.amount,
                "verified_at": timezone.now(),
            },
        )

        if not created:
            if payment.status == "success":
                return True, "already_paid"
            payment.status = "success"
            payment.verified_at = timezone.now()
            payment.save(update_fields=["status", "verified_at"])

        seats = list(order.seats.select_for_update().order_by("seat_number"))
        failed_seats = []

        for seat in seats:
            if seat.is_booked:
                failed_seats.append(seat.seat_number)
                continue

            Booking.objects.create(
                user=order.user,
                movie=order.schedule.movie,
                theater=order.schedule.theater,
                schedule=order.schedule,
                seat=seat,
                payment=payment,
            )
            seat.is_booked = True
            seat.save(update_fields=["is_booked"])

        # Holds are no longer needed either way - seats are now either
        # booked or already lost to someone else.
        SeatReservation.objects.filter(
            schedule=order.schedule, user=order.user, seat__in=seats
        ).delete()

        if failed_seats:
            # Extremely unlikely (these seats were held exclusively for
            # this order's user), but never leave an order marked 'paid'
            # with missing bookings - fail loudly instead.
            order.status = "failed"
            order.save(update_fields=["status"])
            payment.status = "failed"
            payment.error_description = (
                f"Seats no longer available: {', '.join(failed_seats)}"
            )
            payment.save(update_fields=["status", "error_description"])
            return False, "seat_conflict"

        order.status = "paid"
        order.save(update_fields=["status"])

        # transaction.on_commit() defers this until the DB transaction
        # above actually commits - enqueueing inside the transaction
        # itself would risk the Celery worker picking up the task and
        # querying for this Order before the commit is even visible to
        # it. The task enqueue is also wrapped in try/except: if the
        # broker is unreachable, that's logged and swallowed here, not
        # raised - a payment that succeeded must never be turned into an
        # HTTP error just because the email queue is down. The booking
        # itself is already fully committed at this point regardless.
        transaction.on_commit(lambda: _enqueue_ticket_email(order.id))

        return True, "paid"


def _enqueue_ticket_email(order_id):
    try:
        send_ticket_email_task.delay(order_id)
    except Exception:
        # Broker unreachable/unconfigured (e.g. no Redis available on a
        # free host) - fall back to an in-process background thread with
        # its own retry logic instead of just dropping the email. This
        # keeps ticket delivery working even with zero Celery/Redis
        # infrastructure, just without Celery's durability guarantees.
        logger.warning(
            "Celery broker unavailable for order %s - falling back to "
            "in-process background thread for ticket email.",
            order_id,
        )
        background_tasks.send_ticket_email_in_background(order_id)


def _record_payment_failure(order_id, razorpay_payment_id, status, error_code="", error_description=""):
    """
    Records a failed or cancelled payment attempt and immediately releases
    the associated seat holds, so those seats become bookable by other
    users right away instead of waiting out the 2-minute natural expiry.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)

        if order.status == "paid":
            # A success already landed (e.g. via webhook) before this
            # failure signal arrived - don't clobber a real booking.
            return

        payment_id = razorpay_payment_id or f"none-{uuid.uuid4().hex[:12]}"
        payment, created = Payment.objects.get_or_create(
            razorpay_payment_id=payment_id,
            defaults={
                "order": order,
                "status": status,
                "amount": order.amount,
                "error_code": error_code,
                "error_description": error_description,
            },
        )
        if not created:
            payment.status = status
            payment.error_code = error_code
            payment.error_description = error_description
            payment.save(update_fields=["status", "error_code", "error_description"])

        order.status = status
        order.save(update_fields=["status"])

        SeatReservation.objects.filter(
            schedule=order.schedule,
            user=order.user,
            seat__in=order.seats.all(),
        ).delete()


@login_required(login_url="/login/")
def payment_confirm(request, schedule_id):
    """
    Renders the checkout page and creates (or reuses, for a retry) the
    Razorpay Order for the user's currently held seats.
    """
    schedule = get_object_or_404(ShowSchedule, id=schedule_id)

    SeatReservation.clear_expired(schedule=schedule)

    my_reservations = (
        SeatReservation.objects.filter(schedule=schedule, user=request.user)
        .select_related("seat")
        .order_by("seat__seat_number")
    )

    if not my_reservations.exists():
        messages.info(request, "Select and hold your seats before proceeding to payment.")
        return redirect("book_seats", schedule_id=schedule.id)

    seat_ids = set(my_reservations.values_list("seat_id", flat=True))
    total_price = schedule.ticket_price * my_reservations.count()

    # Retry support: if there's already an unresolved order for exactly
    # this same set of held seats, reuse its Razorpay order id rather than
    # creating a new one - this is how Razorpay expects retries to work
    # (multiple payment attempts against one order).
    existing_order = (
        Order.objects.filter(
            user=request.user, schedule=schedule, status__in=["pending", "failed"]
        )
        .order_by("-created_at")
        .first()
    )

    if existing_order and set(existing_order.seats.values_list("id", flat=True)) == seat_ids:
        order = existing_order
        if order.status == "failed":
            order.status = "pending"
            order.save(update_fields=["status"])
    else:
        razorpay_order = razorpay_helpers.create_order(
            amount_rupees=total_price,
            receipt=f"sched{schedule.id}-user{request.user.id}-{uuid.uuid4().hex[:8]}",
        )
        order = Order.objects.create(
            user=request.user,
            schedule=schedule,
            amount=total_price,
            razorpay_order_id=razorpay_order["id"],
        )
        order.seats.set(my_reservations.values_list("seat_id", flat=True))

    return render(
        request,
        "movies/payment_confirm.html",
        {
            "schedule": schedule,
            "reservations": my_reservations,
            "total_price": total_price,
            "amount_paise": int(round(total_price * 100)),
            "order": order,
            "razorpay_key_id": settings_razorpay_key_id(),
            "mock_mode": razorpay_helpers.is_mock_mode(),
        },
    )


def settings_razorpay_key_id():
    from django.conf import settings
    return settings.RAZORPAY_KEY_ID or "rzp_test_mock_key"


@login_required(login_url="/login/")
@require_POST
def verify_payment(request, schedule_id):
    """
    Called by Razorpay Checkout's client-side success handler. Verifies the
    HMAC signature server-side before trusting anything the browser says -
    the browser reporting "success" is never sufficient on its own.
    """
    order_id = request.POST.get("razorpay_order_id")
    payment_id = request.POST.get("razorpay_payment_id")
    signature = request.POST.get("razorpay_signature")

    order = get_object_or_404(Order, razorpay_order_id=order_id, user=request.user)

    if not razorpay_helpers.verify_payment_signature(order_id, payment_id, signature):
        _record_payment_failure(
            order.id, payment_id, "failed",
            error_code="signature_mismatch",
            error_description="Payment signature verification failed.",
        )
        return JsonResponse(
            {"success": False, "error": "Payment verification failed."}, status=400
        )

    ok, reason = _confirm_payment_success(order.id, payment_id)

    if not ok:
        return JsonResponse(
            {"success": False, "error": "Some seats were no longer available.", "reason": reason},
            status=409,
        )

    return JsonResponse({"success": True, "redirect_url": "/profile/"})


@login_required(login_url="/login/")
@require_POST
def payment_failed_or_cancelled(request, schedule_id):
    """
    Called by Razorpay Checkout's client-side failure handler (payment
    actually attempted and declined) and by the page's own Cancel button
    (user backed out before attempting payment at all).
    """
    order_id = request.POST.get("razorpay_order_id")
    payment_id = request.POST.get("razorpay_payment_id", "")
    action = request.POST.get("action", "failed")
    error_code = request.POST.get("error_code", "")
    error_description = request.POST.get("error_description", "")

    status = "cancelled" if action == "cancelled" else "failed"

    order = get_object_or_404(Order, razorpay_order_id=order_id, user=request.user)
    _record_payment_failure(order.id, payment_id, status, error_code, error_description)

    return JsonResponse({"success": True})


@login_required(login_url="/login/")
@require_POST
def mock_simulate_payment(request, schedule_id):
    """
    Only usable when no real Razorpay keys are configured (is_mock_mode()).
    Stands in for what Razorpay Checkout would normally do in the browser:
    generates a fake payment id, signs it the exact same way Razorpay does,
    then runs it through the SAME verify_payment_signature() and
    _confirm_payment_success()/_record_payment_failure() code paths used in
    production - so this proves out the real verification and idempotency
    logic without needing a live Razorpay account.
    """
    if not razorpay_helpers.is_mock_mode():
        return JsonResponse(
            {"success": False, "error": "Mock payments are disabled; real Razorpay keys are configured."},
            status=403,
        )

    order_id = request.POST.get("razorpay_order_id")
    action = request.POST.get("action", "success")
    order = get_object_or_404(Order, razorpay_order_id=order_id, user=request.user)

    fake_payment_id = f"pay_MOCK{uuid.uuid4().hex[:14]}"

    if action == "success":
        signature = razorpay_helpers.sign_mock_payment(order.razorpay_order_id, fake_payment_id)

        if not razorpay_helpers.verify_payment_signature(order.razorpay_order_id, fake_payment_id, signature):
            return JsonResponse({"success": False, "error": "Mock signature failed to verify."}, status=400)

        ok, reason = _confirm_payment_success(order.id, fake_payment_id)
        if not ok:
            return JsonResponse({"success": False, "error": "Seats no longer available.", "reason": reason}, status=409)
        return JsonResponse({"success": True, "redirect_url": "/profile/"})

    status = "cancelled" if action == "cancelled" else "failed"
    _record_payment_failure(
        order.id,
        fake_payment_id if action == "failed" else "",
        status,
        error_code="MOCK_DECLINED" if action == "failed" else "",
        error_description="Simulated failed payment." if action == "failed" else "",
    )
    return JsonResponse({"success": True})


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """
    Server-to-server webhook from Razorpay - the authoritative source of
    truth for payment status, independent of whether the user's browser
    ever calls verify_payment (e.g. they closed the tab right after
    paying). Configure this URL in the Razorpay Dashboard under
    Settings > Webhooks, with events: payment.captured, payment.failed.

    CSRF-exempt because Razorpay's servers can't supply a Django CSRF
    token; the HMAC signature check below is what authenticates the
    request instead.
    """
    signature = request.headers.get("X-Razorpay-Signature", "")
    body = request.body

    if not razorpay_helpers.verify_webhook_signature(body, signature):
        logger.warning("Razorpay webhook: invalid signature, rejecting.")
        return HttpResponseBadRequest("Invalid signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("Invalid payload")

    event = payload.get("event", "")
    entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
    )
    razorpay_order_id = entity.get("order_id")
    razorpay_payment_id = entity.get("id")

    if not razorpay_order_id:
        # Nothing we can act on; acknowledge so Razorpay doesn't keep
        # retrying an event we'll never be able to match.
        return HttpResponse(status=200)

    try:
        order = Order.objects.get(razorpay_order_id=razorpay_order_id)
    except Order.DoesNotExist:
        logger.warning("Razorpay webhook: unknown order_id %s", razorpay_order_id)
        return HttpResponse(status=200)

    if event in ("payment.captured", "order.paid"):
        _confirm_payment_success(order.id, razorpay_payment_id)
    elif event == "payment.failed":
        _record_payment_failure(
            order.id,
            razorpay_payment_id,
            "failed",
            error_code=entity.get("error_code", ""),
            error_description=entity.get("error_description", ""),
        )

    # Always 200 quickly - Razorpay retries on non-2xx, which we don't
    # want once we've already understood (or intentionally ignored) the
    # event.
    return HttpResponse(status=200)


@login_required(login_url="/login/")
@require_POST
def cancel_booking(request, booking_id):
    """
    Lets a user cancel a confirmed, not-yet-watched booking before the
    show starts. Frees the seat immediately and issues a refund (via
    Razorpay, or the mock equivalent) if the booking was actually paid for.
    """
    booking = get_object_or_404(Booking, id=booking_id, user=request.user)

    if booking.is_cancelled:
        messages.info(request, "This booking is already cancelled.")
        return redirect("profile")

    if booking.watched:
        messages.error(request, "You can't cancel a booking you've already watched.")
        return redirect("profile")

    if booking.schedule.show_time <= timezone.now():
        messages.error(request, "This show has already started; cancellation isn't available.")
        return redirect("profile")

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(id=booking.id)

        if booking.is_cancelled:
            return redirect("profile")

        booking.is_cancelled = True
        booking.cancelled_at = timezone.now()
        booking.save(update_fields=["is_cancelled", "cancelled_at"])

        seat = Seat.objects.select_for_update().get(id=booking.seat_id)
        seat.is_booked = False
        seat.save(update_fields=["is_booked"])

        if booking.payment and booking.payment.status == "success":
            refund_amount = booking.schedule.ticket_price
            try:
                refund_response = razorpay_helpers.create_refund(
                    booking.payment.razorpay_payment_id, refund_amount
                )
                Refund.objects.create(
                    booking=booking,
                    amount=refund_amount,
                    status="processed",
                    razorpay_refund_id=refund_response.get("id", ""),
                    processed_at=timezone.now(),
                )
                messages.success(request, "Booking cancelled and refund initiated.")
            except Exception:
                logger.exception("Refund failed for booking %s", booking.id)
                Refund.objects.create(
                    booking=booking, amount=refund_amount, status="failed",
                    reason="Refund API call failed - needs manual review.",
                )
                messages.warning(
                    request,
                    "Booking cancelled, but the automatic refund failed. "
                    "Our team will process it manually.",
                )
        else:
            messages.success(request, "Booking cancelled.")

    return redirect("profile")


@login_required(login_url="/login/")
def download_ticket(request, order_id):
    """
    Lets a user re-download the PDF ticket for any of their own paid
    orders from booking history - generated fresh each time from the
    same generate_ticket_pdf() the email task uses, so it's always
    identical to what was emailed (or would have been, if the email
    failed after all retries).
    """
    order = get_object_or_404(Order, id=order_id)

    if order.user_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied("This isn't your ticket.")

    if order.status != "paid":
        messages.error(request, "This order was never completed, so there's no ticket to download.")
        return redirect("profile")

    from .ticket import generate_ticket_pdf
    pdf_bytes = generate_ticket_pdf(order)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="BMS-{order.id:06d}-ticket.pdf"'
    return response


def verify_ticket(request, order_id, token):
    """
    Public verification page for the ticket's QR code - deliberately does
    NOT require login (a theater staff member scanning a ticket at the
    door isn't logged into the customer's account), but only reveals
    enough to confirm validity, not the customer's personal details.
    """
    try:
        order = Order.objects.select_related(
            "schedule", "schedule__movie", "schedule__theater"
        ).get(id=order_id, verification_token=token)
        valid = order.status == "paid"
    except Order.DoesNotExist:
        order = None
        valid = False

    return render(
        request,
        "movies/verify_ticket.html",
        {
            "order": order,
            "valid": valid,
            "seat_numbers": (
                ", ".join(order.seats.order_by("seat_number").values_list("seat_number", flat=True))
                if order else ""
            ),
        },
    )