import re
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.contrib.auth.models import User
from django.db.models import Avg
from django.utils import timezone


def validate_youtube_url(value):
    """
    Only allow youtube.com / youtu.be URLs for trailers, so the trailer
    field can never be used to embed an arbitrary/untrusted site.
    """
    pattern = re.compile(
        r"^(https?://)?(www\.)?(youtube\.com/(watch\?v=|embed/|shorts/)|youtu\.be/)[\w\-]+",
        re.IGNORECASE,
    )
    if not pattern.match(value):
        raise ValidationError(
            "Please enter a valid YouTube URL "
            "(e.g. https://www.youtube.com/watch?v=... or https://youtu.be/...)."
        )



# Genre Model

class Genre(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


# Language Model

class Language(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name



# Cast Member Model

class CastMember(models.Model):
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=100)
    image = models.ImageField(upload_to="cast/", blank=True, null=True)

    def __str__(self):
        return self.name



# Movie Model

class Movie(models.Model):

    CERTIFICATE_CHOICES = [
        ("U", "U"),
        ("U/A", "U/A"),
        ("A", "A"),
    ]

    name = models.CharField(max_length=255)

    image = models.ImageField(
        upload_to="movies/",
        help_text="Main Poster"
    )

    genre = models.ForeignKey(
        Genre,
        on_delete=models.SET_NULL,
        null=True,
        related_name="movies"
    )

    language = models.ForeignKey(
        Language,
        on_delete=models.SET_NULL,
        null=True,
        related_name="movies"
    )

    cast = models.ManyToManyField(
        CastMember,
        blank=True,
        related_name="movies"
    )

    duration = models.PositiveIntegerField(
        default=120,
        help_text="Duration in Minutes"
    )

    certificate = models.CharField(
        max_length=5,
        choices=CERTIFICATE_CHOICES,
        default="U/A"
    )

    release_date = models.DateField()

    trailer_url = models.URLField(
        blank=True,
        validators=[validate_youtube_url],
        help_text="Paste a YouTube URL (watch, youtu.be, or shorts link)"
    )

    description = models.TextField()

    average_rating = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=0.0
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def update_rating(self):
        avg = self.reviews.aggregate(
            Avg("rating")
        )["rating__avg"]

        self.average_rating = round(avg or 0, 1)
        self.save(update_fields=["average_rating"])

    def get_youtube_id(self):
        """Extract the video ID from a validated YouTube URL."""
        if not self.trailer_url:
            return None

        match = re.search(
            r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([\w\-]+)",
            self.trailer_url,
        )
        return match.group(1) if match else None

    def get_embed_url(self):
        """
        Returns a safe youtube-nocookie.com embed URL built only from the
        extracted video ID — never renders the raw stored URL directly,
        so this can't be used to embed arbitrary iframe sources.
        """
        video_id = self.get_youtube_id()
        if not video_id:
            return None
        return f"https://www.youtube-nocookie.com/embed/{video_id}"

    def review_count(self):
        return self.reviews.count()

    def __str__(self):
        return self.name



# Multiple Movie Posters

class MovieImage(models.Model):

    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        related_name="gallery"
    )

    image = models.ImageField(upload_to="movie_gallery/")

    def __str__(self):
        return f"{self.movie.name} Image"


# Theater Model

class Theater(models.Model):

    name = models.CharField(max_length=255)

    city = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="City this theater is in, e.g. 'Mumbai'. Used for discovery filtering."
    )

    location = models.CharField(
        max_length=255,
        blank=True,
        help_text="Area/address within the city, e.g. 'Andheri West'."
    )

    def __str__(self):
        return self.name



# Show Schedule

class ShowSchedule(models.Model):

    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        related_name="shows"
    )

    theater = models.ForeignKey(
        Theater,
        on_delete=models.CASCADE,
        related_name="shows"
    )

    show_time = models.DateTimeField(db_index=True)

    screen = models.CharField(
        max_length=50,
        default="Screen 1",
        help_text="Screen/auditorium name, printed on the ticket."
    )

    ticket_price = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=250
    )

    def __str__(self):
        return f"{self.movie.name} - {self.theater.name}"



# Seat Model

class Seat(models.Model):

    schedule = models.ForeignKey(
        ShowSchedule,
        on_delete=models.CASCADE,
        related_name="seats"
    )

    seat_number = models.CharField(max_length=10)

    is_booked = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [
            # The occupancy dashboard query counts booked seats per
            # schedule (COUNT(*) WHERE schedule_id=? AND is_booked=?) -
            # a composite index lets that be answered from the index
            # alone without touching the table rows.
            models.Index(fields=["schedule", "is_booked"], name="seat_schedule_booked_idx"),
        ]

    def __str__(self):
        return self.seat_number

    def get_status(self, user=None):
        """
        Returns one of: 'booked', 'reserved_by_you', 'reserved', 'available'.
        Assumes expired reservations have already been cleared by the caller
        (see SeatReservation.clear_expired), so this does no DB queries of
        its own beyond the already-prefetched `reservation` attribute.
        """
        if self.is_booked:
            return "booked"

        reservation = getattr(self, "active_reservation", None)

        if reservation is None:
            return "available"

        if user is not None and reservation.user_id == user.id:
            return "reserved_by_you"

        return "reserved"


# Temporary Seat Reservation (2-minute hold before payment)

class SeatReservation(models.Model):

    HOLD_DURATION_SECONDS = 120

    seat = models.OneToOneField(
        Seat,
        on_delete=models.CASCADE,
        related_name="active_reservation"
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="seat_reservations"
    )

    schedule = models.ForeignKey(
        ShowSchedule,
        on_delete=models.CASCADE,
        related_name="seat_reservations"
    )

    reserved_at = models.DateTimeField(auto_now_add=True)

    expires_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(
                seconds=self.HOLD_DURATION_SECONDS
            )
        super().save(*args, **kwargs)

    def is_expired(self):
        return timezone.now() >= self.expires_at

    def seconds_remaining(self):
        remaining = (self.expires_at - timezone.now()).total_seconds()
        return max(0, int(remaining))

    @classmethod
    def clear_expired(cls, schedule=None):
        """
        Deletes reservations whose hold has expired, freeing those seats
        back to 'available'. Called at the start of every seat-status,
        reserve, and payment request so expiry is enforced immediately
        on next access rather than needing a background worker/cron.
        """
        qs = cls.objects.filter(expires_at__lt=timezone.now())
        if schedule is not None:
            qs = qs.filter(schedule=schedule)
        qs.delete()

    def __str__(self):
        return f"{self.user.username} holding {self.seat.seat_number}"



# Booking Model

class Booking(models.Model):

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )

    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE
    )

    theater = models.ForeignKey(
        Theater,
        on_delete=models.CASCADE
    )

    schedule = models.ForeignKey(
        ShowSchedule,
        on_delete=models.CASCADE
    )

    seat = models.OneToOneField(
        Seat,
        on_delete=models.CASCADE
    )

    booked_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    watched = models.BooleanField(
        default=False
    )

    is_cancelled = models.BooleanField(
        default=False,
        db_index=True,
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    payment = models.ForeignKey(
        "Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
        help_text="The successful payment that confirmed this booking."
    )

    class Meta:
        indexes = [
            # "Most booked movies" / "top theaters" group by movie or
            # theater while excluding cancellations, and "peak booking
            # hours" filters on booked_at - these composite indexes match
            # those exact WHERE/GROUP BY shapes instead of relying on the
            # single-column indexes to be combined at query time.
            models.Index(fields=["movie", "is_cancelled"], name="booking_movie_cancel_idx"),
            models.Index(fields=["theater", "is_cancelled"], name="booking_theater_cancel_idx"),
            models.Index(fields=["schedule", "is_cancelled"], name="booking_schedule_cancel_idx"),
            models.Index(fields=["booked_at", "is_cancelled"], name="booking_date_cancel_idx"),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.movie.name}"



# Refund: tracks a refund issued for a cancelled, previously-paid Booking.
# Kept separate from Payment because one Payment can cover several
# Bookings (multi-seat order) and each seat may be cancelled/refunded
# independently.

class Refund(models.Model):

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processed", "Processed"),
        ("failed", "Failed"),
    ]

    booking = models.OneToOneField(
        Booking,
        on_delete=models.CASCADE,
        related_name="refund"
    )

    amount = models.DecimalField(
        max_digits=8,
        decimal_places=2
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )

    razorpay_refund_id = models.CharField(
        max_length=100,
        blank=True,
    )

    reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Refund for booking #{self.booking_id} - {self.status}"



# Order: one checkout attempt for one or more seats on one show.
# A single Order can have multiple Payment attempts if earlier ones failed
# (retries), but only ever results in Bookings once, when it is marked paid.

class Order(models.Model):

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="orders"
    )

    schedule = models.ForeignKey(
        ShowSchedule,
        on_delete=models.CASCADE,
        related_name="orders"
    )

    seats = models.ManyToManyField(
        Seat,
        related_name="orders"
    )

    amount = models.DecimalField(
        max_digits=8,
        decimal_places=2
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )

    razorpay_order_id = models.CharField(
        max_length=100,
        unique=True
    )

    verification_token = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        help_text="Encoded in the ticket's QR code; lets staff verify a ticket without exposing the numeric order id alone."
    )

    ticket_emailed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set once the ticket email has actually been sent (or last attempted)."
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # Every revenue chart (daily/weekly/monthly/yearly) is:
            # WHERE status='paid' AND created_at BETWEEN ? AND ?
            # grouped by a truncated date - this composite index lets
            # Postgres/SQLite use the index for both the filter and the
            # ordering instead of a full table scan + sort.
            models.Index(fields=["status", "created_at"], name="order_status_created_idx"),
            models.Index(fields=["schedule", "status"], name="order_schedule_status_idx"),
        ]

    def __str__(self):
        return f"Order #{self.id} - {self.user.username} - {self.status}"


# Payment: one attempt to pay for an Order. An Order can have several of
# these if the user retries after a failure - each attempt is recorded
# separately so nothing about a failed try is ever lost.

class Payment(models.Model):

    STATUS_CHOICES = [
        ("created", "Created"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="payments"
    )

    razorpay_payment_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text="Set once Razorpay actually attempts/captures this payment."
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="created",
        db_index=True,
    )

    amount = models.DecimalField(
        max_digits=8,
        decimal_places=2
    )

    error_code = models.CharField(max_length=50, blank=True)
    error_description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Payment {self.razorpay_payment_id or '(pending)'} - {self.status}"



# Review Model

class Review(models.Model):

    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        related_name="reviews"
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )

    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="Rating out of 10."
    )

    review = models.TextField()

    verified_viewer = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        unique_together = ("movie", "user")

    def save(self, *args, **kwargs):

        booking = Booking.objects.filter(
            user=self.user,
            movie=self.movie,
            watched=True
        ).exists()

        self.verified_viewer = booking

        super().save(*args, **kwargs)

        self.movie.update_rating()

    def delete(self, *args, **kwargs):

        movie = self.movie

        super().delete(*args, **kwargs)

        movie.update_rating()

    def __str__(self):
        return f"{self.user.username} - {self.movie.name}"


# Review Report

class ReviewReport(models.Model):

    review = models.ForeignKey(
        Review,
        on_delete=models.CASCADE,
        related_name="reports"
    )

    reported_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )

    reason = models.TextField()

    resolved = models.BooleanField(
        default=False,
        help_text="Mark as resolved once a moderator has reviewed this report."
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"Report by {self.reported_by.username}"



# Recently Viewed: powers the "recently viewed" half of recommendations.
# Upserted (not just created) each time a user opens a movie detail page,
# so viewed_at always reflects the latest view and repeat views don't
# pile up duplicate rows.

class RecentlyViewed(models.Model):

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="recently_viewed"
    )

    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        related_name="viewed_by"
    )

    viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "movie")
        indexes = [
            models.Index(fields=["user", "-viewed_at"], name="recently_viewed_user_idx"),
        ]

    def __str__(self):
        return f"{self.user.username} viewed {self.movie.name}"