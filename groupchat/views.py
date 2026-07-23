from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from chat import klipy
from friends.models import Friendship

from .forms import AddMembersForm, GroupCreateForm
from .models import ChatGroup, GroupMembership, GroupMessage

ALLOWED_MEDIA_HOSTS_SUFFIX = '.klipy.com'


def _membership_or_403(user, group):
    membership = GroupMembership.objects.filter(group=group, user=user).select_related('group').first()
    if not membership:
        return None
    return membership


def _preview_text(message):
    if message is None:
        return None
    if message.kind == GroupMessage.KIND_GIF:
        return '🎬 GIF'
    if message.kind == GroupMessage.KIND_STICKER:
        return '🏷️ Sticker'
    if message.kind == GroupMessage.KIND_SYSTEM:
        return message.body
    who = f'{message.sender.username}: ' if message.sender else ''
    return who + message.body


@login_required
def group_list(request):
    memberships = (
        GroupMembership.objects.filter(user=request.user)
        .select_related('group')
        .order_by('-group__created_at')
    )
    group_ids = [m.group_id for m in memberships]

    last_map = {}
    if group_ids:
        qs = (
            GroupMessage.objects.filter(group_id__in=group_ids)
            .select_related('sender')
            .order_by('-created_at')
        )
        for m in qs.iterator():
            if m.group_id not in last_map:
                last_map[m.group_id] = m
            if len(last_map) == len(group_ids):
                break

    groups = []
    for m in memberships:
        last_msg = last_map.get(m.group_id)
        if m.last_read_at:
            unread = GroupMessage.objects.filter(
                group_id=m.group_id, created_at__gt=m.last_read_at
            ).exclude(sender=request.user).exclude(kind=GroupMessage.KIND_SYSTEM).count()
        else:
            unread = GroupMessage.objects.filter(group_id=m.group_id).exclude(sender=request.user).exclude(
                kind=GroupMessage.KIND_SYSTEM
            ).count()
        groups.append({
            'group': m.group,
            'last_msg': last_msg,
            'last_preview': _preview_text(last_msg),
            'unread': unread,
            'sort_key': last_msg.created_at if last_msg else m.joined_at,
        })
    groups.sort(key=lambda g: g['sort_key'], reverse=True)

    return render(request, 'groupchat/list.html', {'groups': groups})


@login_required
def group_create(request):
    if request.method == 'POST':
        form = GroupCreateForm(request.POST, user=request.user)
        if form.is_valid():
            group = ChatGroup.objects.create(
                name=form.cleaned_data['name'].strip(),
                avatar_color=ChatGroup.random_color(),
                created_by=request.user,
            )
            GroupMembership.objects.create(group=group, user=request.user, is_admin=True, last_read_at=timezone.now())
            member_ids = [int(i) for i in form.cleaned_data['members']]
            names = []
            for uid in member_ids:
                friend = next((f for f in Friendship.friends_of(request.user) if f.pk == uid), None)
                if friend:
                    GroupMembership.objects.get_or_create(group=group, user=friend)
                    names.append(friend.username)
            if names:
                GroupMessage.objects.create(
                    group=group, kind=GroupMessage.KIND_SYSTEM,
                    body=f'{request.user.username} created the group and added {", ".join(names)}.',
                )
            messages.success(request, f'"{group.name}" is ready.')
            return redirect('groupchat:room', group_id=group.pk)
    else:
        form = GroupCreateForm(user=request.user)

    has_friends = bool(Friendship.friends_of(request.user))
    return render(request, 'groupchat/create.html', {'form': form, 'has_friends': has_friends})


@login_required
def group_room(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    membership = _membership_or_403(request.user, group)
    if not membership:
        return HttpResponseForbidden("You're not a member of this group.")

    membership.last_read_at = timezone.now()
    membership.save(update_fields=['last_read_at'])

    group_messages = group.messages.select_related('sender').order_by('created_at')
    member_list = group.memberships.select_related('user').order_by('user__username')

    return render(request, 'groupchat/room.html', {
        'group': group,
        'group_messages': group_messages,
        'members': member_list,
        'member_count': member_list.count(),
        'gif_search_enabled': klipy.is_configured(),
    })


@login_required
def group_poll(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    membership = _membership_or_403(request.user, group)
    if not membership:
        return JsonResponse({'error': 'not a member'}, status=403)

    after_id = int(request.GET.get('after', 0))
    qs = group.messages.filter(pk__gt=after_id).select_related('sender').order_by('created_at')

    data = [
        {
            'id': m.pk,
            'body': m.body,
            'kind': m.kind,
            'media_url': m.media_url,
            'mine': m.sender_id == request.user.pk,
            'sender_username': m.sender.username if m.sender else None,
            'sender_avatar_color': m.sender.avatar_color if m.sender else None,
            'sender_avatar_url': m.sender.avatar_url if m.sender else '',
            'created_at': m.created_at.strftime('%H:%M'),
        }
        for m in qs
    ]
    if data:
        membership.last_read_at = timezone.now()
        membership.save(update_fields=['last_read_at'])

    return JsonResponse({'messages': data})


@login_required
@require_POST
def group_send(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    membership = _membership_or_403(request.user, group)
    if not membership:
        return JsonResponse({'error': 'not a member'}, status=403)

    body = request.POST.get('body', '').strip()
    media_url = request.POST.get('media_url', '').strip()
    kind = request.POST.get('kind', GroupMessage.KIND_TEXT)
    if kind not in (GroupMessage.KIND_GIF, GroupMessage.KIND_STICKER):
        kind = GroupMessage.KIND_TEXT

    if kind == GroupMessage.KIND_TEXT:
        media_url = ''
        if not body:
            return JsonResponse({'error': 'empty message'}, status=400)
    else:
        parsed = urlparse(media_url)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not host.endswith(ALLOWED_MEDIA_HOSTS_SUFFIX):
            return JsonResponse({'error': 'invalid media url'}, status=400)
        body = ''

    if len(body) > 2000:
        body = body[:2000]

    msg = GroupMessage.objects.create(group=group, sender=request.user, body=body, kind=kind, media_url=media_url)
    membership.last_read_at = timezone.now()
    membership.save(update_fields=['last_read_at'])

    return JsonResponse({
        'id': msg.pk,
        'body': msg.body,
        'kind': msg.kind,
        'media_url': msg.media_url,
        'mine': True,
        'sender_username': request.user.username,
        'sender_avatar_color': request.user.avatar_color,
        'sender_avatar_url': request.user.avatar_url,
        'created_at': msg.created_at.strftime('%H:%M'),
    })


@login_required
def group_gif_search(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    if not _membership_or_403(request.user, group):
        return JsonResponse({'error': 'not a member'}, status=403)

    content_type = 'sticker' if request.GET.get('type') == 'sticker' else 'gif'
    query = request.GET.get('q', '').strip()[:100]
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except ValueError:
        page = 1

    if not klipy.is_configured():
        return JsonResponse({'results': [], 'has_next': False, 'error': 'not_configured'})

    try:
        results, has_next = klipy.search(
            content_type=content_type, query=query, page=page,
            customer_id=f'pingback-user-{request.user.pk}',
        )
    except klipy.KlipyError:
        return JsonResponse({'results': [], 'has_next': False, 'error': 'search_failed'})

    return JsonResponse({'results': results, 'has_next': has_next})


@login_required
def group_add_members(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    if not _membership_or_403(request.user, group):
        return HttpResponseForbidden("You're not a member of this group.")

    existing_ids = set(group.memberships.values_list('user_id', flat=True))

    if request.method == 'POST':
        form = AddMembersForm(request.POST, user=request.user, exclude_ids=existing_ids)
        if form.is_valid():
            friends = {f.pk: f for f in Friendship.friends_of(request.user)}
            names = []
            for uid in (int(i) for i in form.cleaned_data['members']):
                friend = friends.get(uid)
                if friend and friend.pk not in existing_ids:
                    GroupMembership.objects.get_or_create(group=group, user=friend)
                    names.append(friend.username)
            if names:
                GroupMessage.objects.create(
                    group=group, kind=GroupMessage.KIND_SYSTEM,
                    body=f'{request.user.username} added {", ".join(names)}.',
                )
                messages.success(request, 'Added to the group.')
            return redirect('groupchat:room', group_id=group.pk)
    else:
        form = AddMembersForm(user=request.user, exclude_ids=existing_ids)

    return render(request, 'groupchat/add_members.html', {'group': group, 'form': form})


@login_required
@require_POST
def group_leave(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    membership = _membership_or_403(request.user, group)
    if not membership:
        return HttpResponseForbidden("You're not a member of this group.")

    membership.delete()
    remaining = group.memberships.count()
    if remaining == 0:
        group.delete()
    else:
        GroupMessage.objects.create(
            group=group, kind=GroupMessage.KIND_SYSTEM,
            body=f'{request.user.username} left the group.',
        )
    messages.success(request, f'You left "{group.name}".')
    return redirect('groupchat:list')
