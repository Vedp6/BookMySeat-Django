from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("movies", "0005_refund_booking_cancelled_at_booking_is_cancelled_and_more"),
    ]

    operations = [
        # auth_user is Django's built-in table - we don't own its model
        # definition, so a raw index is the only way to speed up the
        # "user growth" report's date_joined grouping/filtering without
        # forking the auth app. CREATE INDEX IF NOT EXISTS is safe on
        # both SQLite and Postgres.
        migrations.RunSQL(
            sql="CREATE INDEX IF NOT EXISTS auth_user_date_joined_idx ON auth_user (date_joined);",
            reverse_sql="DROP INDEX IF EXISTS auth_user_date_joined_idx;",
        ),
    ]
