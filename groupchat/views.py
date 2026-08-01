from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import cloudinary_client
from chat import klipy
from friends.models import Friendship

from .forms import AddMembersForm, GroupCreateForm, GroupSettingsForm
from .models import ChatGroup, GroupMembership, GroupMessage, GroupMessageReaction, GroupTypingStatus

ALLOWED_GIF_HOST_SUFFIX = '.klipy.com'
ALLOWED_CHAT_MEDIA_HOST_SUFFIX = '.cloudinary.com'
ALLOWED_EFFECTS = {'confetti', 'balloons', 'fireworks', 'slam', 'loud'}
TYPING_FRESH_SECONDS = 6
MAX_VOICE_SECONDS = 300


def _membership_or_403(user, group):
    return GroupMembership.objects.filter(group=group, user=user).select_related('group').first()


def _preview_text(message):
    if message is None:
        return None
    if message.deleted_at:
        return 'Message deleted'
    if message.kind == GroupMessage.KIND_GIF:
        return '🎬 GIF'
    if message.kind == GroupMessage.KIND_STICKER:
        return '🏷️ Sticker'
    if message.kind == GroupMessage.KIND_VOICE:
        return '🎤 Voice message'
    if message.kind == GroupMessage.KIND_FILE:
        return f'📎 {message.file_name or "File"}'
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
        base_qs = GroupMessage.objects.filter(group_id=m.group_id, deleted_at__isnull=True).exclude(
            sender=request.user
        ).exclude(kind=GroupMessage.KIND_SYSTEM)
        unread = base_qs.filter(created_at__gt=m.last_read_at).count() if m.last_read_at else base_qs.count()
        groups.append({
            'group': m.group,
            'last_msg': last_msg,
            'last_preview': _preview_text(last_msg),
            'unread': 0 if m.is_muted else unread,
            'muted': m.is_muted,
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

    member_list = group.memberships.select_related('user').order_by('user__username')

    return render(request, 'groupchat/room.html', {
        'group': group,
        'members': member_list,
        'member_count': member_list.count(),
        'gif_search_enabled': klipy.is_configured(),
        'is_admin': membership.is_admin,
        'is_muted': membership.is_muted,
    })


def _serialize_reactions(message, user):
    counts = {}
    mine = None
    for r in message.reactions.all():
        counts[r.emoji] = counts.get(r.emoji, 0) + 1
        if r.user_id == user.pk:
            mine = r.emoji
    return [{'emoji': e, 'count': c, 'mine': e == mine} for e, c in counts.items()]


def _serialize_reply(message):
    rt = message.reply_to
    if not rt:
        return None
    return {
        'id': rt.pk,
        'sender_username': rt.sender.username if rt.sender_id else None,
        'preview': (_preview_text(rt) or '')[:80],
    }


def _seen_by_count(message, membership_qs):
    return membership_qs.exclude(user_id=message.sender_id).filter(last_read_at__gte=message.created_at).count()


def _serialize_message(m, request_user, group=None):
    seen_by = None
    if group is not None and m.sender_id == request_user.pk:
        seen_by = _seen_by_count(m, group.memberships.all())
    return {
        'id': m.pk,
        'body': '' if m.deleted_at else m.body,
        'kind': m.kind,
        'media_url': '' if m.deleted_at else m.media_url,
        'file_name': m.file_name,
        'file_size': m.file_size,
        'duration_seconds': m.duration_seconds,
        'effect': '' if m.deleted_at else m.effect,
        'mine': m.sender_id == request_user.pk,
        'sender_username': m.sender.username if m.sender else None,
        'sender_avatar_color': m.sender.avatar_color if m.sender else None,
        'sender_avatar_url': m.sender.avatar_url if m.sender else '',
        'edited': m.edited_at is not None,
        'deleted': m.deleted_at is not None,
        'reply_to': _serialize_reply(m),
        'reactions': _serialize_reactions(m, request_user),
        'seen_by': seen_by,
        'created_at': m.created_at.strftime('%H:%M'),
    }


@login_required
def group_poll(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    membership = _membership_or_403(request.user, group)
    if not membership:
        return JsonResponse({'error': 'not a member'}, status=403)

    after_id = int(request.GET.get('after', 0))
    qs = group.messages.filter(pk__gt=after_id).select_related(
        'sender', 'reply_to', 'reply_to__sender'
    ).prefetch_related('reactions').order_by('created_at')

    data = [_serialize_message(m, request.user, group=group) for m in qs]
    if data:
        membership.last_read_at = timezone.now()
        membership.save(update_fields=['last_read_at'])

    recent_ids = list(group.messages.order_by('-id').values_list('id', flat=True)[:40])
    recent_msgs = group.messages.filter(pk__in=recent_ids).select_related('sender')
    reaction_updates = {m.pk: _serialize_reactions(m, request.user) for m in recent_msgs}
    edit_delete_updates = {
        m.pk: {'edited': m.edited_at is not None, 'deleted': m.deleted_at is not None, 'body': '' if m.deleted_at else m.body}
        for m in recent_msgs
    }
    seen_by_updates = {
        m.pk: _seen_by_count(m, group.memberships.all())
        for m in recent_msgs if m.sender_id == request.user.pk
    }

    typers = list(
        GroupTypingStatus.objects.filter(
            group=group, updated_at__gte=timezone.now() - timezone.timedelta(seconds=TYPING_FRESH_SECONDS)
        ).exclude(user=request.user).select_related('user').values_list('user__username', flat=True)
    )

    return JsonResponse({
        'messages': data,
        'reaction_updates': reaction_updates,
        'edit_delete_updates': edit_delete_updates,
        'seen_by_updates': seen_by_updates,
        'typing_usernames': typers,
    })


@login_required
@require_POST
def group_typing(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    if not _membership_or_403(request.user, group):
        return JsonResponse({'error': 'not a member'}, status=403)
    GroupTypingStatus.objects.update_or_create(group=group, user=request.user, defaults={})
    return JsonResponse({'ok': True})


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
    if kind not in (GroupMessage.KIND_GIF, GroupMessage.KIND_STICKER, GroupMessage.KIND_VOICE, GroupMessage.KIND_FILE):
        kind = GroupMessage.KIND_TEXT

    effect = request.POST.get('effect', '') if kind == GroupMessage.KIND_TEXT else ''
    if effect not in ALLOWED_EFFECTS:
        effect = ''

    reply_to = None
    reply_to_id = request.POST.get('reply_to')
    if reply_to_id:
        reply_to = group.messages.filter(pk=reply_to_id, deleted_at__isnull=True).first()

    file_name, file_size, duration_seconds = '', None, None

    if kind == GroupMessage.KIND_TEXT:
        media_url = ''
        if not body:
            return JsonResponse({'error': 'empty message'}, status=400)
    else:
        parsed = urlparse(media_url)
        host = parsed.hostname or ''
        allowed_suffix = ALLOWED_GIF_HOST_SUFFIX if kind in (GroupMessage.KIND_GIF, GroupMessage.KIND_STICKER) else ALLOWED_CHAT_MEDIA_HOST_SUFFIX
        if parsed.scheme != 'https' or not host.endswith(allowed_suffix):
            return JsonResponse({'error': 'invalid media url'}, status=400)
        body = ''

        if kind == GroupMessage.KIND_FILE:
            file_name = request.POST.get('file_name', '')[:255]
            try:
                file_size = int(request.POST.get('file_size', 0)) or None
            except ValueError:
                file_size = None
        if kind == GroupMessage.KIND_VOICE:
            try:
                duration_seconds = min(int(request.POST.get('duration_seconds', 0)), MAX_VOICE_SECONDS) or None
            except ValueError:
                duration_seconds = None

    if len(body) > 2000:
        body = body[:2000]

    msg = GroupMessage.objects.create(
        group=group, sender=request.user, body=body, kind=kind, media_url=media_url,
        file_name=file_name, file_size=file_size, duration_seconds=duration_seconds, reply_to=reply_to,
        effect=effect,
    )
    membership.last_read_at = timezone.now()
    membership.save(update_fields=['last_read_at'])
    GroupTypingStatus.objects.filter(group=group, user=request.user).delete()

    return JsonResponse(_serialize_message(msg, request.user, group=group))


@login_required
@require_POST
def group_upload_voice(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    if not _membership_or_403(request.user, group):
        return JsonResponse({'error': 'not a member'}, status=403)
    audio_file = request.FILES.get('audio')
    if not audio_file:
        return JsonResponse({'error': 'missing audio'}, status=400)
    if audio_file.size > cloudinary_client.MAX_CHAT_FILE_BYTES:
        return JsonResponse({'error': 'file too large'}, status=400)
    try:
        url, public_id = cloudinary_client.upload_chat_voice(audio_file, request.user.pk)
    except cloudinary_client.CloudinaryError as exc:
        return JsonResponse({'error': str(exc)}, status=502)
    return JsonResponse({'media_url': url, 'public_id': public_id})


@login_required
@require_POST
def group_upload_file(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    if not _membership_or_403(request.user, group):
        return JsonResponse({'error': 'not a member'}, status=403)
    up = request.FILES.get('file')
    if not up:
        return JsonResponse({'error': 'missing file'}, status=400)
    if up.size > cloudinary_client.MAX_CHAT_FILE_BYTES:
        return JsonResponse({'error': 'file too large (25MB max)'}, status=400)
    try:
        url, public_id = cloudinary_client.upload_chat_file(up, request.user.pk)
    except cloudinary_client.CloudinaryError as exc:
        return JsonResponse({'error': str(exc)}, status=502)
    return JsonResponse({'media_url': url, 'public_id': public_id, 'file_name': up.name, 'file_size': up.size})


@login_required
@require_POST
def group_edit_message(request, message_id):
    msg = get_object_or_404(GroupMessage, pk=message_id, sender=request.user)
    if msg.deleted_at:
        return JsonResponse({'error': 'message deleted'}, status=400)
    if msg.kind != GroupMessage.KIND_TEXT:
        return JsonResponse({'error': 'only text messages can be edited'}, status=400)
    body = request.POST.get('body', '').strip()
    if not body:
        return JsonResponse({'error': 'empty message'}, status=400)
    msg.body = body[:2000]
    msg.edited_at = timezone.now()
    msg.save(update_fields=['body', 'edited_at'])
    return JsonResponse(_serialize_message(msg, request.user, group=msg.group))


@login_required
@require_POST
def group_delete_message(request, message_id):
    msg = get_object_or_404(GroupMessage, pk=message_id, sender=request.user)
    if not msg.deleted_at:
        if msg.media_public_id:
            resource_type = 'video' if msg.kind == GroupMessage.KIND_VOICE else 'auto'
            cloudinary_client.delete_chat_media(msg.media_public_id, resource_type=resource_type)
        msg.deleted_at = timezone.now()
        msg.body = ''
        msg.media_url = ''
        msg.save(update_fields=['deleted_at', 'body', 'media_url'])
    return JsonResponse(_serialize_message(msg, request.user, group=msg.group))


@login_required
@require_POST
def group_react_message(request, message_id):
    msg = get_object_or_404(GroupMessage, pk=message_id)
    if not _membership_or_403(request.user, msg.group):
        return JsonResponse({'error': 'forbidden'}, status=403)

    emoji = request.POST.get('emoji', '').strip()[:8]
    if not emoji:
        return JsonResponse({'error': 'missing emoji'}, status=400)

    existing = GroupMessageReaction.objects.filter(message=msg, user=request.user).first()
    if existing and existing.emoji == emoji:
        existing.delete()
    else:
        GroupMessageReaction.objects.update_or_create(message=msg, user=request.user, defaults={'emoji': emoji})

    return JsonResponse({'reactions': _serialize_reactions(msg, request.user)})


@login_required
def group_search_messages(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    if not _membership_or_403(request.user, group):
        return JsonResponse({'error': 'not a member'}, status=403)

    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': []})

    qs = group.messages.filter(
        body__icontains=query, deleted_at__isnull=True, kind=GroupMessage.KIND_TEXT
    ).select_related('sender').order_by('-created_at')[:30]

    return JsonResponse({'results': [
        {
            'id': m.pk, 'body': m.body, 'sender_username': m.sender.username if m.sender else '',
            'mine': m.sender_id == request.user.pk, 'created_at': m.created_at.strftime('%b %d, %H:%M'),
        }
        for m in qs
    ]})


@login_required
@require_POST
def group_toggle_mute(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    membership = _membership_or_403(request.user, group)
    if not membership:
        return JsonResponse({'error': 'not a member'}, status=403)
    membership.is_muted = not membership.is_muted
    membership.save(update_fields=['is_muted'])
    return JsonResponse({'muted': membership.is_muted})


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
def group_settings(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    membership = _membership_or_403(request.user, group)
    if not membership:
        return HttpResponseForbidden("You're not a member of this group.")

    if request.method == 'POST' and 'save_settings' in request.POST:
        if not membership.is_admin:
            return HttpResponseForbidden('Only group admins can change these settings.')
        form = GroupSettingsForm(request.POST, request.FILES)
        if form.is_valid():
            new_name = form.cleaned_data['name'].strip()
            if new_name and new_name != group.name:
                group.name = new_name
            photo = form.cleaned_data.get('photo')
            if photo:
                try:
                    url, public_id = cloudinary_client.upload_group_photo(photo, group.pk)
                    group.avatar_url = url
                    group.avatar_public_id = public_id
                except cloudinary_client.CloudinaryError as exc:
                    messages.error(request, f"Couldn't upload that photo: {exc}")
                    return render(request, 'groupchat/settings.html', {
                        'group': group, 'form': form, 'membership': membership,
                        'member_list': group.memberships.select_related('user').order_by('-is_admin', 'user__username'),
                        'cloudinary_configured': cloudinary_client.is_configured(),
                    })
            group.save()
            messages.success(request, 'Group updated.')
            return redirect('groupchat:settings', group_id=group.pk)
    else:
        form = GroupSettingsForm(initial={'name': group.name})

    member_list = group.memberships.select_related('user').order_by('-is_admin', 'user__username')
    return render(request, 'groupchat/settings.html', {
        'group': group, 'form': form, 'membership': membership, 'member_list': member_list,
        'cloudinary_configured': cloudinary_client.is_configured(),
    })


@login_required
@require_POST
def group_remove_member(request, group_id, user_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    membership = _membership_or_403(request.user, group)
    if not membership or not membership.is_admin:
        return HttpResponseForbidden('Only group admins can remove members.')

    target = get_object_or_404(GroupMembership, group=group, user_id=user_id)
    if target.user_id == request.user.pk:
        return HttpResponseForbidden("Use 'Leave group' to remove yourself.")

    username = target.user.username
    target.delete()
    GroupMessage.objects.create(
        group=group, kind=GroupMessage.KIND_SYSTEM,
        body=f'{request.user.username} removed {username} from the group.',
    )
    messages.success(request, f'Removed {username} from the group.')
    return redirect('groupchat:settings', group_id=group.pk)


@login_required
@require_POST
def group_set_admin(request, group_id, user_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    membership = _membership_or_403(request.user, group)
    if not membership or not membership.is_admin:
        return HttpResponseForbidden('Only group admins can do that.')

    target = get_object_or_404(GroupMembership, group=group, user_id=user_id)
    make_admin = request.POST.get('make_admin') == '1'

    if not make_admin and target.user_id == request.user.pk:
        if group.memberships.filter(is_admin=True).count() <= 1:
            messages.error(request, "You're the only admin — promote someone else first.")
            return redirect('groupchat:settings', group_id=group.pk)

    target.is_admin = make_admin
    target.save(update_fields=['is_admin'])
    messages.success(request, f'{target.user.username} is {"now an admin" if make_admin else "no longer an admin"}.')
    return redirect('groupchat:settings', group_id=group.pk)


@login_required
@require_POST
def group_leave(request, group_id):
    group = get_object_or_404(ChatGroup, pk=group_id)
    membership = _membership_or_403(request.user, group)
    if not membership:
        return HttpResponseForbidden("You're not a member of this group.")

    was_admin = membership.is_admin
    membership.delete()
    remaining = group.memberships.select_related('user').order_by('joined_at')

    if not remaining.exists():
        group.delete()
    else:
        if was_admin and not remaining.filter(is_admin=True).exists():
            successor = remaining.first()
            successor.is_admin = True
            successor.save(update_fields=['is_admin'])
            GroupMessage.objects.create(
                group=group, kind=GroupMessage.KIND_SYSTEM,
                body=f'{request.user.username} left the group. {successor.user.username} is now an admin.',
            )
        else:
            GroupMessage.objects.create(
                group=group, kind=GroupMessage.KIND_SYSTEM,
                body=f'{request.user.username} left the group.',
            )
    messages.success(request, f'You left "{group.name}".')
    return redirect('groupchat:list')
