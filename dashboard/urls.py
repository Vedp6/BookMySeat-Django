from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_home, name='dashboard_home'),
    path('export/bookings.csv', views.export_raw_bookings_csv, name='export_raw_bookings_csv'),
    path('export/<str:report>.csv', views.export_csv, name='export_csv'),
]
