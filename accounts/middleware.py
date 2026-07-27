from django.contrib.auth import get_user_model
from django.utils import timezone

# How often we bother writing last_seen_at. It doesn't need to be exact —
# nobody notices "online" lagging by up to a minute — and this keeps a busy
# session from hitting the DB on every single request.
LAST_SEEN_UPDATE_INTERVAL = 60  # seconds

# "Online" means "seen within this window". Kept here (rather than only in
# templates) so every place that shows an online dot uses the same rule.
ONLINE_WINDOW_SECONDS = 90


class LastSeenMiddleware:
    """
    Updates request.user.last_seen_at on authenticated requests, throttled
    so it's a cheap .update() call rather than a full model save with
    signals, and skipped entirely once per minute rather than per request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            now = timezone.now()
            stale = user.last_seen_at is None or (now - user.last_seen_at).total_seconds() > LAST_SEEN_UPDATE_INTERVAL
            if stale:
                # .update() on the queryset, not user.save() — avoids
                # touching every other field / running save signals just to
                # bump a timestamp on virtually every request. Note: we
                # can't use type(user) here — request.user is a Django
                # SimpleLazyObject wrapper, not the real model class.
                get_user_model().objects.filter(pk=user.pk).update(last_seen_at=now)
                user.last_seen_at = now

        return response
