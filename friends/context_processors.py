from .models import FriendRequest


def pending_requests_count(request):
    if request.user.is_authenticated:
        count = FriendRequest.objects.filter(to_user=request.user).count()
        return {'pending_requests_count': count}
    return {'pending_requests_count': 0}
