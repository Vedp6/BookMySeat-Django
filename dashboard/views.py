import csv
from datetime import datetime, timedelta

from django.shortcuts import render
from django.utils import timezone
from django.http import HttpResponse, StreamingHttpResponse

from movies.models import Booking
from . import analytics
from .decorators import admin_required


def _parse_date_range(request):
    """
    Reads start_date/end_date (YYYY-MM-DD) from the querystring, defaults
    to the last 30 days if not given, and returns both the plain dates
    (for re-populating the filter form) and timezone-aware datetimes
    spanning the full first/last day (for querying).
    """
    today = timezone.localdate()
    end_str = request.GET.get("end_date")
    start_str = request.GET.get("start_date")

    try:
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else today
    except ValueError:
        end_date = today

    try:
        start_date = (
            datetime.strptime(start_str, "%Y-%m-%d").date()
            if start_str
            else end_date - timedelta(days=29)
        )
    except ValueError:
        start_date = end_date - timedelta(days=29)

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))

    granularity = request.GET.get("granularity", "day")
    if granularity not in ("day", "week", "month", "year"):
        granularity = "day"

    return start_date, end_date, start_dt, end_dt, granularity


@admin_required
def dashboard_home(request):
    start_date, end_date, start_dt, end_dt, granularity = _parse_date_range(request)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "granularity": granularity,
        "kpis": analytics.summary_kpis(start_dt, end_dt),
        "revenue_series": analytics.revenue_over_time(start_dt, end_dt, granularity),
        "booking_series": analytics.booking_trends(start_dt, end_dt, granularity),
        "occupancy": analytics.theater_occupancy(start_dt, end_dt),
        "top_movies": analytics.most_booked_movies(start_dt, end_dt),
        "top_theaters": analytics.top_theaters(start_dt, end_dt),
        "peak_hours": analytics.peak_booking_hours(start_dt, end_dt),
        "cancellation_stats": analytics.cancellation_refund_stats(start_dt, end_dt),
        "user_growth_series": analytics.user_growth(start_dt, end_dt, granularity),
    }
    return render(request, "dashboard/home.html", context)


class _EchoBuffer:
    """A file-like object whose .write() just returns what it's given -
    lets csv.writer be used to produce chunks for StreamingHttpResponse
    instead of building the whole CSV in memory first."""
    def write(self, value):
        return value


REPORT_BUILDERS = {
    "revenue": lambda s, e, g: (
        ["Period", "Gross Revenue", "Refunded", "Net Revenue", "Orders"],
        [
            [row["period"], row["revenue"], row["refunded"], row["net_revenue"], row["orders"]]
            for row in analytics.revenue_over_time(s, e, g)
        ],
    ),
    "bookings": lambda s, e, g: (
        ["Period", "Tickets Booked"],
        [[row["period"], row["count"]] for row in analytics.booking_trends(s, e, g)],
    ),
    "occupancy": lambda s, e, g: (
        ["Theater", "Total Seats", "Booked Seats", "Occupancy %"],
        [
            [row["theater"], row["total_seats"], row["booked_seats"], row["occupancy_pct"]]
            for row in analytics.theater_occupancy(s, e)
        ],
    ),
    "movies": lambda s, e, g: (
        ["Movie", "Bookings"],
        [[row["movie__name"], row["bookings"]] for row in analytics.most_booked_movies(s, e, limit=1000)],
    ),
    "theaters": lambda s, e, g: (
        ["Theater", "Revenue", "Orders"],
        [
            [row["schedule__theater__name"], row["revenue"], row["orders"]]
            for row in analytics.top_theaters(s, e, limit=1000)
        ],
    ),
    "peak_hours": lambda s, e, g: (
        ["Hour of Day", "Bookings"],
        [[row["hour"], row["count"]] for row in analytics.peak_booking_hours(s, e)],
    ),
    "user_growth": lambda s, e, g: (
        ["Period", "New Users"],
        [[row["period"], row["count"]] for row in analytics.user_growth(s, e, g)],
    ),
}


@admin_required
def export_csv(request, report):
    """
    Exports any of the aggregated reports above as CSV. These result sets
    are already small (one row per day/theater/movie, not per booking),
    so building them in memory here is safe even at 100k+ underlying
    bookings - the aggregation itself happened in the database.
    """
    start_date, end_date, start_dt, end_dt, granularity = _parse_date_range(request)

    builder = REPORT_BUILDERS.get(report)
    if builder is None:
        return HttpResponse("Unknown report type.", status=404)

    header, rows = builder(start_dt, end_dt, granularity)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{report}_{start_date}_{end_date}.csv"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


def _raw_bookings_rows(start_dt, end_dt):
    """
    Yields CSV lines for every individual booking in range, one at a time,
    using .iterator() so Django never materialises the full queryset (or
    even a full page of ORM model instances) in memory at once - this is
    the one export where the row count actually scales with the number of
    bookings, so it's the one that needs streaming rather than the
    "build a small list, return it" approach used for the aggregated
    reports above.
    """
    writer = csv.writer(_EchoBuffer())
    yield writer.writerow(
        ["Booking ID", "User", "Movie", "Theater", "Seat", "Amount", "Status", "Booked At"]
    )

    qs = (
        Booking.objects.filter(booked_at__range=(start_dt, end_dt))
        .select_related("user", "movie", "theater", "seat", "payment")
        .values_list(
            "id", "user__username", "movie__name", "theater__name",
            "seat__seat_number", "payment__amount", "is_cancelled", "booked_at",
        )
        .iterator(chunk_size=2000)
    )

    for booking_id, username, movie_name, theater_name, seat_number, amount, is_cancelled, booked_at in qs:
        status = "Cancelled" if is_cancelled else "Confirmed"
        yield writer.writerow(
            [booking_id, username, movie_name, theater_name, seat_number, amount, status, booked_at]
        )


@admin_required
def export_raw_bookings_csv(request):
    start_date, end_date, start_dt, end_dt, _ = _parse_date_range(request)

    response = StreamingHttpResponse(
        _raw_bookings_rows(start_dt, end_dt), content_type="text/csv"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="bookings_{start_date}_{end_date}.csv"'
    )
    return response
