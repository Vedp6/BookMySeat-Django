from functools import wraps
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied


def _is_dashboard_admin(user):
  
    return user.is_authenticated and user.is_staff


def admin_required(view_func):
    """
    Requires the user be logged in AND be staff (or superuser). Anonymous
    users are redirected to login; authenticated non-staff users get a
    403, not a silent redirect, so it's obvious access was denied rather
    than looking like a broken link.
    """
    @wraps(view_func)
    @login_required(login_url="/login/")
    def wrapped(request, *args, **kwargs):
        if not _is_dashboard_admin(request.user):
            raise PermissionDenied("You do not have access to the admin dashboard.")
        return view_func(request, *args, **kwargs)
    return wrapped
