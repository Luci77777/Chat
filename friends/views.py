from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .models import FriendRequest, Friendship

User = get_user_model()


@login_required
def search_users(request):
    query = request.GET.get('q', '').strip()
    results = []
    if query:
        results = (
            User.objects.filter(username__icontains=query)
            .exclude(pk=request.user.pk)
            .order_by('username')[:25]
        )
        # Attach relationship status for each result so the template can
        # show the right button (Add / Pending / Friends).
        sent_ids = set(FriendRequest.objects.filter(from_user=request.user).values_list('to_user_id', flat=True))
        received_ids = set(FriendRequest.objects.filter(to_user=request.user).values_list('from_user_id', flat=True))
        friend_ids = {f.pk for f in Friendship.friends_of(request.user)}

        for u in results:
            if u.pk in friend_ids:
                u.relation = 'friends'
            elif u.pk in sent_ids:
                u.relation = 'pending_sent'
            elif u.pk in received_ids:
                u.relation = 'pending_received'
            else:
                u.relation = 'none'

    return render(request, 'friends/search.html', {'query': query, 'results': results})


@login_required
def friend_requests(request):
    incoming = FriendRequest.objects.filter(to_user=request.user).select_related('from_user').order_by('-created_at')
    outgoing = FriendRequest.objects.filter(from_user=request.user).select_related('to_user').order_by('-created_at')
    return render(request, 'friends/requests.html', {'incoming': incoming, 'outgoing': outgoing})


@login_required
def friend_list(request):
    friends = Friendship.friends_of(request.user)
    return render(request, 'friends/list.html', {'friends': friends})


@login_required
def send_request(request, username):
    target = get_object_or_404(User, username=username)
    if target == request.user:
        messages.error(request, "You can't friend yourself.")
    elif Friendship.are_friends(request.user, target):
        messages.info(request, f'You and {target.username} are already friends.')
    elif FriendRequest.objects.filter(from_user=target, to_user=request.user).exists():
        # They already sent us one — just accept it instead of duplicating.
        FriendRequest.objects.filter(from_user=target, to_user=request.user).delete()
        Friendship.create(request.user, target)
        messages.success(request, f'You and {target.username} are now friends!')
    else:
        _, created = FriendRequest.objects.get_or_create(from_user=request.user, to_user=target)
        if created:
            messages.success(request, f'Friend request sent to {target.username}.')
    return redirect(request.META.get('HTTP_REFERER', 'friends:search'))


@login_required
def accept_request(request, pk):
    fr = get_object_or_404(FriendRequest, pk=pk, to_user=request.user)
    Friendship.create(fr.from_user, fr.to_user)
    messages.success(request, f'You and {fr.from_user.username} are now friends!')
    fr.delete()
    return redirect('friends:requests')


@login_required
def decline_request(request, pk):
    fr = get_object_or_404(FriendRequest, pk=pk, to_user=request.user)
    fr.delete()
    messages.info(request, 'Friend request declined.')
    return redirect('friends:requests')


@login_required
def cancel_request(request, pk):
    fr = get_object_or_404(FriendRequest, pk=pk, from_user=request.user)
    fr.delete()
    messages.info(request, 'Friend request cancelled.')
    return redirect('friends:requests')


@login_required
def remove_friend(request, username):
    other = get_object_or_404(User, username=username)
    a, b = sorted([request.user, other], key=lambda u: u.pk)
    Friendship.objects.filter(user_a=a, user_b=b).delete()
    messages.info(request, f'Removed {other.username} from your friends.')
    return redirect('friends:list')
