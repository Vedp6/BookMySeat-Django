from django.contrib import admin
from .models import (
    Genre,
    Language,
    CastMember,
    Movie,
    MovieImage,
    Theater,
    ShowSchedule,
    Seat,
    SeatReservation,
    Order,
    Payment,
    Booking,
    Refund,
    RecentlyViewed,
    Review,
    ReviewReport,
)



# Inline Movie Images

class MovieImageInline(admin.TabularInline):
    model = MovieImage
    extra = 1



# Genre

@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


# Language

@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)



# Cast Member

@admin.register(CastMember)
class CastMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "role")
    search_fields = ("name", "role")



# Movie

@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "genre",
        "language",
        "certificate",
        "duration",
        "average_rating",
        "release_date",
    )

    list_filter = (
        "genre",
        "language",
        "certificate",
    )

    search_fields = (
        "name",
        "description",
    )

    filter_horizontal = ("cast",)

    inlines = [MovieImageInline]



# Movie Images

@admin.register(MovieImage)
class MovieImageAdmin(admin.ModelAdmin):
    list_display = ("movie",)



# Theater

@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "city",
        "location",
    )

    list_filter = (
        "city",
    )

    search_fields = (
        "name",
        "city",
        "location",
    )



# Show Schedule

@admin.register(ShowSchedule)
class ShowScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "movie",
        "theater",
        "show_time",
        "ticket_price",
    )

    list_filter = (
        "movie",
        "theater",
    )

    actions = ["generate_seats"]

    @admin.action(description="Generate seats A1-J10 for selected shows")
    def generate_seats(self, request, queryset):
        rows = "ABCDEFGHIJ"
        created_count = 0

        for schedule in queryset:
            existing = set(
                Seat.objects.filter(schedule=schedule).values_list(
                    "seat_number", flat=True
                )
            )
            new_seats = [
                Seat(schedule=schedule, seat_number=f"{row}{num}")
                for row in rows
                for num in range(1, 11)
                if f"{row}{num}" not in existing
            ]
            Seat.objects.bulk_create(new_seats)
            created_count += len(new_seats)

        self.message_user(request, f"Created {created_count} seat(s).")



# Seat

@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = (
        "schedule",
        "seat_number",
        "is_booked",
    )

    list_filter = (
        "is_booked",
    )


@admin.register(SeatReservation)
class SeatReservationAdmin(admin.ModelAdmin):
    list_display = (
        "seat",
        "user",
        "schedule",
        "reserved_at",
        "expires_at",
    )

    list_filter = (
        "schedule",
    )

    search_fields = (
        "user__username",
        "seat__seat_number",
    )


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = (
        "razorpay_payment_id",
        "status",
        "amount",
        "error_code",
        "error_description",
        "created_at",
        "verified_at",
    )
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "schedule",
        "amount",
        "status",
        "razorpay_order_id",
        "created_at",
    )

    list_filter = (
        "status",
    )

    search_fields = (
        "user__username",
        "razorpay_order_id",
    )

    inlines = [PaymentInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "razorpay_payment_id",
        "order",
        "status",
        "amount",
        "created_at",
        "verified_at",
    )

    list_filter = (
        "status",
    )

    search_fields = (
        "razorpay_payment_id",
        "order__user__username",
    )


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = (
        "booking",
        "amount",
        "status",
        "razorpay_refund_id",
        "created_at",
        "processed_at",
    )

    list_filter = (
        "status",
    )

    search_fields = (
        "booking__user__username",
        "razorpay_refund_id",
    )

    actions = ["mark_processed"]

    @admin.action(description="Mark selected refunds as processed")
    def mark_processed(self, request, queryset):
        from django.utils import timezone
        queryset.update(status="processed", processed_at=timezone.now())

@admin.register(RecentlyViewed)
class RecentlyViewedAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "movie",
        "viewed_at",
    )

    search_fields = (
        "user__username",
        "movie__name",
    )



# Booking

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "movie",
        "theater",
        "schedule",
        "seat",
        "payment",
        "watched",
        "is_cancelled",
        "booked_at",
    )

    list_filter = (
        "watched",
        "is_cancelled",
    )

    search_fields = (
        "user__username",
        "movie__name",
    )

    actions = ["mark_watched"]

    @admin.action(description="Mark selected bookings as watched")
    def mark_watched(self, request, queryset):
        queryset.update(watched=True)



# Review

@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "movie",
        "rating",
        "verified_viewer",
        "created_at",
    )

    list_filter = (
        "rating",
        "verified_viewer",
    )

    search_fields = (
        "user__username",
        "movie__name",
    )



# Review Report

@admin.register(ReviewReport)
class ReviewReportAdmin(admin.ModelAdmin):
    list_display = (
        "review",
        "reported_by",
        "resolved",
        "created_at",
    )

    list_filter = (
        "resolved",
    )

    search_fields = (
        "reported_by__username",
        "review__movie__name",
    )

    actions = ["mark_resolved", "delete_reported_review"]

    @admin.action(description="Mark selected reports as resolved")
    def mark_resolved(self, request, queryset):
        queryset.update(resolved=True)

    @admin.action(description="Delete the reported review(s)")
    def delete_reported_review(self, request, queryset):
        for report in queryset:
            report.review.delete()
        queryset.delete()