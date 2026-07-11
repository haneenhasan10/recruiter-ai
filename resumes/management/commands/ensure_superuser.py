import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Create or update the admin account from DJANGO_SUPERUSER_* env vars.

    Unlike `createsuperuser --noinput`, this always syncs the password to
    the current env var value, even if the account already exists - so
    updating DJANGO_SUPERUSER_PASSWORD and redeploying actually takes effect.
    """

    help = "Create or update the superuser from DJANGO_SUPERUSER_* env vars."

    def handle(self, *args, **options):
        username = os.getenv("DJANGO_SUPERUSER_USERNAME")
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "")
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD")

        if not username or not password:
            self.stdout.write("DJANGO_SUPERUSER_USERNAME/PASSWORD not set - skipping.")
            return

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username, defaults={"email": email}
        )
        user.email = email or user.email
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()

        self.stdout.write(
            f"Superuser '{username}' {'created' if created else 'updated'}."
        )
