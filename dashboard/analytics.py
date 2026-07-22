"""
All dashboard analytics queries live here, separate from views.py.

Design principle for every function below: do the aggregation in the
database (Sum/Count/Avg + GROUP BY via .values().annotate()) and return
only the small, already-summarized result set. None of these functions
loop over Booking/Order/Payment model instances in Python or call
list(queryset) on a raw per-row queryset - that's what keeps this fast
and memory-safe at 100,000+ bookings: the amount of data pulled out of
the database is proportional to the number of *groups* (days, theaters,
movies - typically dozens to low hundreds of rows), not the number of
underlying transactions.
"""

from django.contrib.auth.models import User
from django.db.models import Sum, Count
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncYear, ExtractHour

from movies.models import Order, Booking, Seat, Refund

TRUNC_FUNCS = {
    "day": TruncDate,
    "week": TruncWeek,
    "month": TruncMonth,
    "year": TruncYear,
}


def revenue_over_time(start_dt, end_dt, granularity="day"):
    """
    Gross revenue, refunds, and net revenue, grouped by day/week/month/year.

    Gross revenue for a period = paid Orders created in that period.
    Refunds for a period = Refunds processed in that period (which may be
    a different, later period than the original sale - refunds are
    recorded against the day they were issued, not retroactively
    subtracted from the original sale's day, matching standard revenue
    reporting practice). Net = Gross - Refunds for that same period.
    """
    trunc = TRUNC_FUNCS.get(granularity, TruncDate)

    gross_by_period = {
        row["period"]: row
        for row in (
            Order.objects.filter(status="paid", created_at__range=(start_dt, end_dt))
            .annotate(period=trunc("created_at"))
            .values("period")
            .annotate(revenue=Sum("amount"), orders=Count("id"))
        )
    }

    refunds_by_period = {
        row["period"]: row["refunded"]
        for row in (
            Refund.objects.filter(status="processed", processed_at__range=(start_dt, end_dt))
            .annotate(period=trunc("processed_at"))
            .values("period")
            .annotate(refunded=Sum("amount"))
        )
    }

    all_periods = sorted(set(gross_by_period) | set(refunds_by_period))
    results = []
    for period in all_periods:
        gross = gross_by_period.get(period, {}).get("revenue") or 0
        orders = gross_by_period.get(period, {}).get("orders") or 0
        refunded = refunds_by_period.get(period, 0) or 0
        results.append(
            {
                "period": period,
                "revenue": gross,
                "refunded": refunded,
                "net_revenue": gross - refunded,
                "orders": orders,
            }
        )
    return results


def booking_trends(start_dt, end_dt, granularity="day"):
    """Ticket (seat) count, grouped by day/week/month/year."""
    trunc = TRUNC_FUNCS.get(granularity, TruncDate)
    return list(
        Booking.objects.filter(is_cancelled=False, booked_at__range=(start_dt, end_dt))
        .annotate(period=trunc("booked_at"))
        .values("period")
        .annotate(count=Count("id"))
        .order_by("period")
    )


def theater_occupancy(start_dt, end_dt):
    """
    Occupancy % per theater for shows scheduled in the given range.

    Deliberately two small GROUP BY queries merged in Python rather than
    one query with two joined Counts - annotating two separate reverse
    relations (all seats, and booked seats) on the same queryset causes
    join fan-out in the ORM that silently inflates both counts. Two
    single-join aggregates avoid that entirely while still doing all the
    actual counting in the database.
    """
    total_qs = (
        Seat.objects.filter(schedule__show_time__range=(start_dt, end_dt))
        .values("schedule__theater_id", "schedule__theater__name")
        .annotate(total_seats=Count("id"))
    )
    booked_qs = (
        Seat.objects.filter(
            schedule__show_time__range=(start_dt, end_dt), is_booked=True
        )
        .values("schedule__theater_id")
        .annotate(booked_seats=Count("id"))
    )
    booked_map = {row["schedule__theater_id"]: row["booked_seats"] for row in booked_qs}

    results = []
    for row in total_qs:
        theater_id = row["schedule__theater_id"]
        total = row["total_seats"]
        booked = booked_map.get(theater_id, 0)
        occupancy_pct = round((booked / total) * 100, 1) if total else 0
        results.append(
            {
                "theater": row["schedule__theater__name"],
                "total_seats": total,
                "booked_seats": booked,
                "occupancy_pct": occupancy_pct,
            }
        )
    results.sort(key=lambda r: -r["occupancy_pct"])
    return results


def most_booked_movies(start_dt, end_dt, limit=10):
    return list(
        Booking.objects.filter(is_cancelled=False, booked_at__range=(start_dt, end_dt))
        .values("movie_id", "movie__name")
        .annotate(bookings=Count("id"))
        .order_by("-bookings")[:limit]
    )


def top_theaters(start_dt, end_dt, limit=10):
    """Ranked by revenue (from paid Orders, not per-Booking, to avoid
    double-counting orders that cover multiple seats)."""
    return list(
        Order.objects.filter(status="paid", created_at__range=(start_dt, end_dt))
        .values("schedule__theater_id", "schedule__theater__name")
        .annotate(revenue=Sum("amount"), orders=Count("id"))
        .order_by("-revenue")[:limit]
    )


def peak_booking_hours(start_dt, end_dt):
    """Bookings grouped by hour-of-day (0-23), across the whole range."""
    return list(
        Booking.objects.filter(is_cancelled=False, booked_at__range=(start_dt, end_dt))
        .annotate(hour=ExtractHour("booked_at"))
        .values("hour")
        .annotate(count=Count("id"))
        .order_by("hour")
    )


def cancellation_refund_stats(start_dt, end_dt):
    total_bookings = Booking.objects.filter(booked_at__range=(start_dt, end_dt)).count()
    cancelled = Booking.objects.filter(
        is_cancelled=True, cancelled_at__range=(start_dt, end_dt)
    ).count()
    rate = round((cancelled / total_bookings) * 100, 1) if total_bookings else 0

    refund_agg = Refund.objects.filter(created_at__range=(start_dt, end_dt)).aggregate(
        total_amount=Sum("amount"), count=Count("id")
    )
    refund_by_status = list(
        Refund.objects.filter(created_at__range=(start_dt, end_dt))
        .values("status")
        .annotate(count=Count("id"), amount=Sum("amount"))
    )

    return {
        "total_bookings": total_bookings,
        "cancelled_bookings": cancelled,
        "cancellation_rate": rate,
        "total_refund_amount": refund_agg["total_amount"] or 0,
        "refund_count": refund_agg["count"] or 0,
        "refund_by_status": refund_by_status,
    }


def user_growth(start_dt, end_dt, granularity="day"):
    trunc = TRUNC_FUNCS.get(granularity, TruncDate)
    return list(
        User.objects.filter(date_joined__range=(start_dt, end_dt))
        .annotate(period=trunc("date_joined"))
        .values("period")
        .annotate(count=Count("id"))
        .order_by("period")
    )


def summary_kpis(start_dt, end_dt):
    revenue_agg = Order.objects.filter(
        status="paid", created_at__range=(start_dt, end_dt)
    ).aggregate(total=Sum("amount"), orders=Count("id"))

    refund_agg = Refund.objects.filter(
        status="processed", processed_at__range=(start_dt, end_dt)
    ).aggregate(total=Sum("amount"))

    total_bookings = Booking.objects.filter(
        is_cancelled=False, booked_at__range=(start_dt, end_dt)
    ).count()

    new_users = User.objects.filter(date_joined__range=(start_dt, end_dt)).count()

    gross_revenue = revenue_agg["total"] or 0
    total_refunds = refund_agg["total"] or 0
    net_revenue = gross_revenue - total_refunds
    total_orders = revenue_agg["orders"] or 0
    avg_order_value = round(gross_revenue / total_orders, 2) if total_orders else 0

    return {
        "gross_revenue": gross_revenue,
        "total_refunds": total_refunds,
        "net_revenue": net_revenue,
        "total_orders": total_orders,
        "total_bookings": total_bookings,
        "new_users": new_users,
        "avg_order_value": avg_order_value,
    }
