import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookmyseat.settings")

app = Celery("bookmyseat")

# Read CELERY_* settings from Django's settings.py (see settings.py for
# CELERY_BROKER_URL / CELERY_RESULT_BACKEND).
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in every installed app (movies/tasks.py).
app.autodiscover_tasks()
