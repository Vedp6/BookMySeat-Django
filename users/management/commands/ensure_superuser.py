import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """
    Creates a superuser from DJANGO_SUPERUSER_USERNAME / _EMAIL / _PASSWORD
    environment variables, for hosts (like Render's free tier) that don't
    give you Shell access to run `createsuperuser` interactively.

    Safe to run on every deploy: does nothing if the user already exists,
    so it can just be added to build.sh permanently rather than needing to
    be run manually once and then removed.
    """

    help = "Creates a superuser from env vars if one doesn't already exist."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")

        if not username or not password:
            self.stdout.write(
                "DJANGO_SUPERUSER_USERNAME/PASSWORD not set - skipping "
                "superuser creation."
            )
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Superuser '{username}' already exists - skipping.")
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created superuser '{username}'."))
