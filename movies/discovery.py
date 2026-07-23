

from datetime import datetime, time as dt_time

from django.db.models import Q, Count, Min
from django.db.models.functions import ExtractHour
from django.utils import timezone

from .models import Movie, Booking, RecentlyViewed, Theater

PAGE_SIZE = 12

TIME_SLOTS = {
    "morning": (0, 12),     # before 12:00
    "afternoon": (12, 17),  # 12:00 - 17:00
    "evening": (17, 21),    # 17:00 - 21:00
    "night": (21, 24),      # after 21:00
}

SORT_OPTIONS = {
    "popularity": "-booking_count",
    "newest": "-release_date",
    "rating": "-average_rating",
    "price_low": "min_price",
    "price_high": "-min_price",
}


def get_filter_choices():
    """Distinct values for populating filter dropdowns - small, cheap
    queries run once per page load, not per-row."""
    from .models import Genre, Language

    return {
        "genres": Genre.objects.order_by("name"),
        "languages": Language.objects.order_by("name"),
        "theaters": Theater.objects.order_by("name"),
        "cities": Theater.objects.exclude(city="").values_list("city", flat=True).distinct().order_by("city"),
    }


def build_movie_queryset(params):
    """
    Builds the filtered/sorted/annotated Movie queryset for the discovery
    page from a dict-like of query params (typically request.GET).

    Every branch below only ever touches the fields it needs and defers
    to the database for filtering/counting/sorting - nothing here loads
    full Movie objects into Python just to filter or count them.
    """
    qs = Movie.objects.select_related("genre", "language")
    needs_distinct = False

    search = (params.get("q") or "").strip()
    if search:
        qs = qs.filter(name__icontains=search)

    genre_id = params.get("genre")
    if genre_id:
        qs = qs.filter(genre_id=genre_id)

    language_id = params.get("language")
    if language_id:
        qs = qs.filter(language_id=language_id)

    min_rating = params.get("min_rating")
    if min_rating:
        try:
            qs = qs.filter(average_rating__gte=float(min_rating))
        except ValueError:
            pass

    release_from = params.get("release_from")
    if release_from:
        try:
            qs = qs.filter(release_date__gte=datetime.strptime(release_from, "%Y-%m-%d").date())
        except ValueError:
            pass

    release_to = params.get("release_to")
    if release_to:
        try:
            qs = qs.filter(release_date__lte=datetime.strptime(release_to, "%Y-%m-%d").date())
        except ValueError:
            pass

    city = (params.get("city") or "").strip()
    if city:
        qs = qs.filter(shows__theater__city__iexact=city)
        needs_distinct = True

    theater_id = params.get("theater")
    if theater_id:
        qs = qs.filter(shows__theater_id=theater_id)
        needs_distinct = True

    show_date = params.get("show_date")
    if show_date:
        try:
            d = datetime.strptime(show_date, "%Y-%m-%d").date()
            qs = qs.filter(shows__show_time__date=d)
            needs_distinct = True
        except ValueError:
            pass

    time_slot = params.get("time_slot")
    if time_slot in TIME_SLOTS:
        start_hour, end_hour = TIME_SLOTS[time_slot]
        qs = qs.filter(
            shows__show_time__hour__gte=start_hour,
            shows__show_time__hour__lt=end_hour,
        )
        needs_distinct = True

    if needs_distinct:
        qs = qs.distinct()

    # Annotations needed for sorting - Count uses distinct=True so it's
    # correct even after the joins added above; Min is unaffected by
    # duplicate rows either way.
    qs = qs.annotate(
        booking_count=Count(
            "booking", filter=Q(booking__is_cancelled=False), distinct=True
        ),
        min_price=Min("shows__ticket_price"),
    )

    sort = params.get("sort", "popularity")
    order_field = SORT_OPTIONS.get(sort, SORT_OPTIONS["popularity"])
    qs = qs.order_by(order_field, "-id")

    return qs


def paginate(queryset, page_number, page_size=PAGE_SIZE):
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

    paginator = Paginator(queryset, page_size)
    try:
        page = paginator.page(page_number)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages) if paginator.num_pages else paginator.page(1)
    return paginator, page


def track_recently_viewed(user, movie):
    if not user.is_authenticated:
        return
    RecentlyViewed.objects.update_or_create(
        user=user, movie=movie, defaults={"viewed_at": timezone.now()}
    )


def recommended_for_user(user, limit=10):
    """
    "Recommended for You": movies sharing a genre or language with what
    the user has booked or recently viewed, ranked by overall popularity.

    Deliberately does NOT exclude already-booked/viewed movies from the
    results - a movie a user just booked is often exactly what they want
    to keep seeing (rewatching, showing friends, checking showtimes again),
    so it stays eligible to appear rather than disappearing the moment
    they book it. Falls back to globally trending movies for users with
    no history yet (new accounts, anonymous browsing).
    """
    if not user.is_authenticated:
        return trending_fallback(limit)

    booked_movie_ids = set(
        Booking.objects.filter(user=user, is_cancelled=False)
        .values_list("movie_id", flat=True)
        .distinct()
    )
    viewed_movie_ids = set(
        RecentlyViewed.objects.filter(user=user)
        .order_by("-viewed_at")
        .values_list("movie_id", flat=True)[:15]
    )
    seed_ids = booked_movie_ids | viewed_movie_ids

    if not seed_ids:
        return trending_fallback(limit)

    seed_genre_ids = set(
        Movie.objects.filter(id__in=seed_ids).values_list("genre_id", flat=True)
    )
    seed_language_ids = set(
        Movie.objects.filter(id__in=seed_ids).values_list("language_id", flat=True)
    )

    recommended = list(
        Movie.objects.select_related("genre", "language")
        .filter(Q(genre_id__in=seed_genre_ids) | Q(language_id__in=seed_language_ids))
        .annotate(
            booking_count=Count(
                "booking", filter=Q(booking__is_cancelled=False), distinct=True
            )
        )
        .order_by("-booking_count", "-average_rating")[:limit]
    )

    if len(recommended) < limit:
        # Not enough personalized matches (small catalog / niche taste) -
        # top up with trending movies not already in the list.
        have_ids = seed_ids | {m.id for m in recommended}
        top_up = trending_fallback(limit - len(recommended), exclude_ids=have_ids)
        recommended.extend(top_up)

    return recommended


def trending_fallback(limit, exclude_ids=None):
    qs = Movie.objects.select_related("genre", "language").annotate(
        booking_count=Count("booking", filter=Q(booking__is_cancelled=False), distinct=True)
    )
    if exclude_ids:
        qs = qs.exclude(id__in=exclude_ids)
    return list(qs.order_by("-booking_count", "-average_rating")[:limit])
