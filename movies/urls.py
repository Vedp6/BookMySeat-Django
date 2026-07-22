from django.urls import path
from . import views
urlpatterns=[
    path('',views.movie_list,name='movie_list'),
    path('<int:movie_id>/',views.movie_detail,name='movie_detail'),
    path('<int:movie_id>/theaters',views.theater_list,name='theater_list'),
    path('schedule/<int:schedule_id>/seats/book/',views.book_seats,name='book_seats'),
    path('schedule/<int:schedule_id>/seats/status/',views.seat_status,name='seat_status'),
    path('schedule/<int:schedule_id>/seats/reserve/',views.reserve_seat,name='reserve_seat'),
    path('schedule/<int:schedule_id>/payment/',views.payment_confirm,name='payment_confirm'),
    path('schedule/<int:schedule_id>/payment/verify/',views.verify_payment,name='verify_payment'),
    path('schedule/<int:schedule_id>/payment/failed/',views.payment_failed_or_cancelled,name='payment_failed_or_cancelled'),
    path('schedule/<int:schedule_id>/payment/mock/',views.mock_simulate_payment,name='mock_simulate_payment'),
    path('payment/webhook/',views.razorpay_webhook,name='razorpay_webhook'),
    path('booking/<int:booking_id>/cancel/',views.cancel_booking,name='cancel_booking'),
    path('order/<int:order_id>/ticket/download/',views.download_ticket,name='download_ticket'),
    path('verify-ticket/<int:order_id>/<uuid:token>/',views.verify_ticket,name='verify_ticket'),
    path('review/<int:review_id>/edit/',views.edit_review,name='edit_review'),
    path('review/<int:review_id>/delete/',views.delete_review,name='delete_review'),
    path('review/<int:review_id>/report/',views.report_review,name='report_review'),
]