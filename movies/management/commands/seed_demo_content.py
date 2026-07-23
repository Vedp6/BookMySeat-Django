"""
Seeds original, fictional demo content: movies (with generated poster
art, not real film posters), theaters, show schedules, seats, users,
bookings, reviews, and some cancellations/refunds - so Discovery filters,
Recommendations, and the Admin Dashboard all have real data to show
instead of looking empty.

Idempotent: checks for one specific seeded movie by name before doing
anything, so it's safe to leave this call in build.sh permanently - it
will only actually create data once, on the first deploy after this is
added, and silently do nothing on every deploy after that.

Deliberately does NOT touch, modify, or remove any existing data -
purely additive, per the "don't change the project" constraint.
"""

import io
import random
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from movies.models import (
    Genre, Language, Movie, Theater, ShowSchedule, Seat,
    Booking, Order, Payment, Review, Refund,
)

MARKER_MOVIE_NAME = "Crimson Horizon"  # idempotency check

GENRE_COLORS = {
    "Action": ("#8B0000", "#2C0000"),
    "Comedy": ("#DDA015", "#7A5200"),
    "Drama": ("#2C3E50", "#0D1218"),
    "Thriller": ("#1B1B2F", "#050508"),
    "Sci-Fi": ("#003B46", "#001417"),
    "Romance": ("#A13D63", "#4A1B2E"),
    "Horror": ("#1A0000", "#000000"),
    "Mystery": ("#2E1A47", "#12081E"),
}

MOVIES_DATA = [
    # (title, genre, language, certificate, duration, days_since_release, description)
    ("Crimson Horizon", "Action", "English", "U/A", 142, 12,
     "A former special-forces pilot is pulled back into action when a rogue satellite threatens global communications, racing against time across three continents."),
    ("Laughing Stock", "Comedy", "Hindi", "U", 118, 5,
     "A struggling stand-up comedian accidentally becomes a viral sensation for all the wrong reasons, and has to decide whether fame is worth losing himself."),
    ("The Quiet River", "Drama", "English", "U/A", 128, 40,
     "Three generations of a family return to their ancestral home by the river, confronting old wounds none of them ever really healed."),
    ("Midnight Ledger", "Thriller", "English", "A", 121, 8,
     "An forensic accountant uncovers a decades-old fraud that reaches the top of her own firm, and must decide who she can still trust."),
    ("Beyond the Static", "Sci-Fi", "English", "U/A", 137, 60,
     "In a near-future where memories can be traded like currency, a data smuggler discovers a memory that was never supposed to exist."),
    ("Monsoon Letters", "Romance", "Hindi", "U", 132, 20,
     "Two strangers exchange letters through a mysteriously shared mailbox for a full monsoon season before ever learning each other's names."),
    ("The Hollow House", "Horror", "English", "A", 104, 3,
     "A family restoring an old countryside estate discovers the previous owners never actually left."),
    ("Nine Unopened Doors", "Mystery", "English", "U/A", 124, 55,
     "A locked-room mystery aboard a stranded luxury train, where every passenger has a reason to have wanted the victim gone."),
    ("Second Innings", "Drama", "Tamil", "U", 138, 30,
     "A retired cricketer reluctantly coaches a struggling village team, rediscovering why he fell in love with the game in the first place."),
    ("Static Bloom", "Sci-Fi", "Telugu", "U/A", 129, 70,
     "A botanist and an AI research an ancient seed vault that may hold the key to reversing an ecological collapse, if they can agree on how to use it."),
    ("The Last Encore", "Drama", "English", "U/A", 119, 15,
     "An aging rock musician plans one final tour, forcing a long-estranged daughter to decide whether to join the band she once fled."),
    ("Chaos Theory Wedding", "Comedy", "Hindi", "U", 126, 25,
     "Two feuding families are forced to plan a wedding together in 48 hours after the original planner disappears with the deposit."),
]

THEATERS_DATA = [
    ("PVR Horizon", "Mumbai", "Andheri West"),
    ("INOX Metro Walk", "Delhi", "Saket"),
    ("Cinepolis Forum", "Bangalore", "Koramangala"),
    ("AGS Cinemas", "Chennai", "T. Nagar"),
    ("City Pride Multiplex", "Pune", "Kothrud"),
]

SCREENS = ["Screen 1", "Screen 2", "Screen 3", "IMAX", "Gold Class"]

REVIEW_TEMPLATES = [
    (9, "Genuinely one of the better releases this year. The pacing never drags and the ending actually earns its emotional beats."),
    (8, "Really solid watch. A couple of slow stretches in the middle but the performances carry it through."),
    (10, "Went in with low expectations and left completely won over. Would watch again."),
    (7, "Good, not great. Worth a watch on a weekend but nothing groundbreaking."),
    (6, "Decent time-pass. The trailer oversold it a bit, but still enjoyable in the theater."),
    (9, "The direction and cinematography alone make this worth the ticket price."),
    (5, "Mixed feelings. Some genuinely great scenes surrounded by a lot of filler."),
    (8, "Took my whole family, everyone enjoyed it for different reasons. Good crowd-pleaser."),
    (4, "Expected more given the buzz. Felt overly long for the story being told."),
    (10, "Best theater experience I've had in a while. The sound design in the big scenes was incredible."),
]

DEMO_USERNAMES = [
    "aarav_sharma", "diya_patel", "kabir_reddy", "ananya_iyer", "vihaan_nair",
    "ishita_rao", "arjun_menon", "sneha_kapoor", "rohan_das", "meera_pillai",
]


def generate_poster(title, genre):
    """Generates an original, non-infringing poster: a gradient background
    (colored by genre) with the movie title overlaid - no real film art."""
    top_color, bottom_color = GENRE_COLORS.get(genre, ("#333333", "#111111"))

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    top_rgb = hex_to_rgb(top_color)
    bottom_rgb = hex_to_rgb(bottom_color)

    width, height = 500, 750
    img = Image.new("RGB", (width, height), top_rgb)
    draw = ImageDraw.Draw(img)

    for y in range(height):
        ratio = y / height
        r = int(top_rgb[0] * (1 - ratio) + bottom_rgb[0] * ratio)
        g = int(top_rgb[1] * (1 - ratio) + bottom_rgb[1] * ratio)
        b = int(top_rgb[2] * (1 - ratio) + bottom_rgb[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    try:
        font_large = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42
        )
        font_small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22
        )
    except IOError:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    words = title.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font_large)
        if bbox[2] - bbox[0] > width - 80 and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)

    total_text_height = len(lines) * 55
    y_start = height // 2 - total_text_height // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font_large)
        text_width = bbox[2] - bbox[0]
        draw.text(
            ((width - text_width) / 2, y_start + i * 55),
            line, font=font_large, fill=(255, 255, 255),
        )

    draw.text((width / 2 - 40, height - 60), genre.upper(), font=font_small, fill=(220, 220, 220))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    buf.seek(0)
    return SimpleUploadedFile(
        f"{title.lower().replace(' ', '_')}.jpg", buf.read(), content_type="image/jpeg"
    )


class Command(BaseCommand):
    help = "Seeds original demo movies, theaters, reviews, and booking history. Safe to run repeatedly - idempotent."

    def handle(self, *args, **options):
        if Movie.objects.filter(name=MARKER_MOVIE_NAME).exists():
            self.stdout.write("Demo content already seeded - skipping.")
            return

        random.seed(42)
        now = timezone.now()

        with transaction.atomic():
            genres = {g: Genre.objects.get_or_create(name=g)[0] for g in GENRE_COLORS}
            languages = {
                lang: Language.objects.get_or_create(name=lang)[0]
                for lang in ["English", "Hindi", "Tamil", "Telugu"]
            }

            movies = []
            for title, genre, lang, cert, duration, days_ago, desc in MOVIES_DATA:
                movie = Movie.objects.create(
                    name=title,
                    image=generate_poster(title, genre),
                    genre=genres[genre],
                    language=languages[lang],
                    duration=duration,
                    certificate=cert,
                    release_date=date.today() - timedelta(days=days_ago),
                    description=desc,
                )
                movies.append(movie)
            self.stdout.write(f"Created {len(movies)} movies.")

            theaters = [
                Theater.objects.create(name=name, city=city, location=loc)
                for name, city, loc in THEATERS_DATA
            ]
            self.stdout.write(f"Created {len(theaters)} theaters.")

            users = []
            for uname in DEMO_USERNAMES:
                u, created = User.objects.get_or_create(
                    username=uname, defaults={"email": f"{uname}@example.com"}
                )
                if created:
                    u.set_unusable_password()
                    u.save()
                users.append(u)
            self.stdout.write(f"Created/found {len(users)} demo users.")

            schedules = []
            for movie in movies:
                num_shows = random.randint(3, 6)
                for _ in range(num_shows):
                    theater = random.choice(theaters)
                    is_past = random.random() < 0.6
                    if is_past:
                        show_time = now - timedelta(
                            days=random.randint(1, 40), hours=random.randint(0, 20)
                        )
                    else:
                        show_time = now + timedelta(
                            days=random.randint(1, 14), hours=random.randint(0, 20)
                        )
                    schedule = ShowSchedule.objects.create(
                        movie=movie, theater=theater,
                        screen=random.choice(SCREENS),
                        show_time=show_time,
                        ticket_price=random.choice([180, 200, 220, 250, 280, 320]),
                    )
                    seats = [
                        Seat(schedule=schedule, seat_number=f"{row}{n}")
                        for row in "ABCDE" for n in range(1, 9)
                    ]
                    Seat.objects.bulk_create(seats)
                    schedules.append((schedule, is_past))
            self.stdout.write(f"Created {len(schedules)} show schedules with seats.")

            booking_count = 0
            review_count = 0
            refund_count = 0

            for schedule, is_past in schedules:
                if not is_past:
                    continue  # only past shows get booking/review history

                available_seats = list(Seat.objects.filter(schedule=schedule))
                num_bookings = random.randint(2, 6)
                booked_seats = random.sample(
                    available_seats, min(num_bookings, len(available_seats))
                )

                for seat in booked_seats:
                    user = random.choice(users)
                    is_cancelled = random.random() < 0.08

                    razorpay_order_id = f"order_SEEDDEMO{booking_count:06d}"
                    order = Order.objects.create(
                        user=user, schedule=schedule, amount=schedule.ticket_price,
                        status="paid", razorpay_order_id=razorpay_order_id,
                    )
                    order.seats.add(seat)
                    payment = Payment.objects.create(
                        order=order, razorpay_payment_id=f"pay_SEEDDEMO{booking_count:06d}",
                        status="success", amount=schedule.ticket_price,
                        verified_at=schedule.show_time,
                    )
                    booking = Booking.objects.create(
                        user=user, movie=schedule.movie, theater=schedule.theater,
                        schedule=schedule, seat=seat, payment=payment,
                        watched=not is_cancelled, is_cancelled=is_cancelled,
                    )
                    seat.is_booked = not is_cancelled
                    seat.save(update_fields=["is_booked"])
                    booking_count += 1

                    # backdate timestamps (auto_now_add ignores values at
                    # creation time, so update them after the fact)
                    Booking.objects.filter(id=booking.id).update(
                        booked_at=schedule.show_time - timedelta(days=random.randint(1, 5))
                    )
                    Order.objects.filter(id=order.id).update(
                        created_at=schedule.show_time - timedelta(days=random.randint(1, 5))
                    )

                    if is_cancelled:
                        Refund.objects.create(
                            booking=booking, amount=schedule.ticket_price,
                            status="processed",
                            razorpay_refund_id=f"rfnd_SEEDDEMO{refund_count:06d}",
                            processed_at=schedule.show_time,
                        )
                        refund_count += 1
                    elif random.random() < 0.7:
                        if Review.objects.filter(user=user, movie=schedule.movie).exists():
                            continue
                        rating, review_text = random.choice(REVIEW_TEMPLATES)
                        # jitter the rating slightly so not every review of
                        # a given text is identical
                        rating = max(1, min(10, rating + random.choice([-1, 0, 0, 1])))
                        Review.objects.create(
                            user=user, movie=schedule.movie,
                            rating=rating, review=review_text,
                        )
                        schedule.movie.update_rating()
                        review_count += 1

            self.stdout.write(self.style.SUCCESS(
                f"Done. Created {booking_count} bookings, {review_count} reviews, "
                f"{refund_count} refunds across {len(movies)} movies and "
                f"{len(theaters)} theaters."
            ))
