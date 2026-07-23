import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from friends.models import Friendship
from groupchat.models import GroupMembership
from . import metered
from .models import Call, GroupCall, GroupCallParticipant, GroupCallSignal

User = get_user_model()

# Mesh calls need one WebRTC connection per *pair* of participants
# (N*(N-1)/2 of them), so cap group size to keep that from getting silly on
# an average phone/laptop. 8 participants = 28 simultaneous connections per
# device at the limit, already a lot to ask of a mesh — this is meant for
# small-group calls, not broadcast/webinar-scale ones.
MAX_GROUP_CALL_PARTICIPANTS = 8

# A participant who hasn't heartbeated in this long is treated as gone
# (tab closed, phone locked, network died) even without an explicit "leave".
STALE_AFTER_SECONDS = 25


def _serialize(call, user):
    other = call.callee if call.caller_id == user.pk else call.caller
    return {
        'id': call.pk,
        'kind': call.kind,
        'status': call.status,
        'is_caller': call.caller_id == user.pk,
        'other_username': other.username,
        'other_avatar_color': other.avatar_color,
        'other_avatar_url': other.avatar_url,
        'offer_sdp': call.offer_sdp,
        'answer_sdp': call.answer_sdp,
        'created_at': call.created_at.isoformat(),
    }


@login_required
def ice_servers(request):
    """
    Login-gated so our (rate-limited, free-tier) Metered quota isn't exposed
    to the open internet — only authenticated members of this app get
    TURN credentials handed to them.
    """
    return JsonResponse({'ice_servers': metered.get_ice_servers()})


@login_required
@require_POST
def start_call(request, username):
    friend = get_object_or_404(User, username=username)
    if not Friendship.are_friends(request.user, friend):
        return JsonResponse({'error': 'not friends'}, status=403)

    kind = request.POST.get('kind')
    if kind not in (Call.KIND_AUDIO, Call.KIND_VIDEO):
        return JsonResponse({'error': 'invalid kind'}, status=400)

    # NOTE: don't .strip() the SDP itself — a valid SDP's last line must end
    # with a trailing CRLF, and str.strip() removes that terminator, which
    # made strict WebRTC SDP parsers (e.g. Chrome) throw "Invalid SDP line"
    # on the final attribute line and kill call setup. Only check-for-blank
    # on a stripped copy; store the original untouched.
    offer_sdp = request.POST.get('offer_sdp', '')
    if not offer_sdp.strip():
        return JsonResponse({'error': 'missing offer_sdp'}, status=400)

    existing = Call.objects.filter(
        Q(caller=request.user, callee=friend) | Q(caller=friend, callee=request.user),
        status__in=Call.ACTIVE_STATUSES,
    ).first()
    if existing:
        return JsonResponse({'error': 'already_in_call', 'call_id': existing.pk}, status=409)

    call = Call.objects.create(caller=request.user, callee=friend, kind=kind, offer_sdp=offer_sdp)
    return JsonResponse(_serialize(call, request.user))


@login_required
def call_status(request, call_id):
    call = get_object_or_404(Call, pk=call_id)
    if request.user.pk not in (call.caller_id, call.callee_id):
        return JsonResponse({'error': 'not a participant'}, status=403)
    return JsonResponse(_serialize(call, request.user))


@login_required
def incoming_call(request):
    """
    Polled (only when notify_summary flags has_incoming_call=True) to fetch
    full details for the ringing call so the UI can show who's calling.
    """
    call = (
        Call.objects.filter(callee=request.user, status=Call.STATUS_RINGING)
        .select_related('caller')
        .order_by('-created_at')
        .first()
    )
    if not call:
        return JsonResponse({'call': None})
    return JsonResponse({'call': _serialize(call, request.user)})


@login_required
@require_POST
def accept_call(request, call_id):
    call = get_object_or_404(Call, pk=call_id)
    if request.user.pk != call.callee_id:
        return JsonResponse({'error': 'not the callee'}, status=403)
    if call.status != Call.STATUS_RINGING:
        return JsonResponse({'error': 'call not ringing', 'status': call.status}, status=409)

    # Same reasoning as start_call: don't strip the SDP itself, only check
    # for blank on a stripped copy — stripping the real value corrupts the
    # required trailing CRLF on its last line.
    answer_sdp = request.POST.get('answer_sdp', '')
    if not answer_sdp.strip():
        return JsonResponse({'error': 'missing answer_sdp'}, status=400)

    call.answer_sdp = answer_sdp
    call.status = Call.STATUS_ACCEPTED
    call.save(update_fields=['answer_sdp', 'status', 'updated_at'])
    return JsonResponse(_serialize(call, request.user))


@login_required
@require_POST
def decline_call(request, call_id):
    call = get_object_or_404(Call, pk=call_id)
    if request.user.pk != call.callee_id:
        return JsonResponse({'error': 'not the callee'}, status=403)
    if call.status != Call.STATUS_RINGING:
        return JsonResponse({'error': 'call not ringing', 'status': call.status}, status=409)

    call.status = Call.STATUS_DECLINED
    call.ended_at = timezone.now()
    call.save(update_fields=['status', 'ended_at', 'updated_at'])
    return JsonResponse(_serialize(call, request.user))


@login_required
@require_POST
def end_call(request, call_id):
    call = get_object_or_404(Call, pk=call_id)
    if request.user.pk not in (call.caller_id, call.callee_id):
        return JsonResponse({'error': 'not a participant'}, status=403)

    if call.status in Call.ACTIVE_STATUSES:
        # A call that rings out with nobody accepting is "missed"; anything
        # already connected that gets hung up is just "ended".
        call.status = Call.STATUS_MISSED if call.status == Call.STATUS_RINGING else Call.STATUS_ENDED
        call.ended_at = timezone.now()
        call.save(update_fields=['status', 'ended_at', 'updated_at'])

    return JsonResponse(_serialize(call, request.user))

# ---------------------------------------------------------------------------
# Group calls (mesh, small groups) — see GroupCall/GroupCallParticipant/
# GroupCallSignal in calls/models.py for the shape of this.
# ---------------------------------------------------------------------------

def _group_membership_or_403(user, group_id):
    return GroupMembership.objects.filter(group_id=group_id, user=user).first()


def _reap_stale_participants(call):
    cutoff = timezone.now() - datetime.timedelta(seconds=STALE_AFTER_SECONDS)
    call.participants.filter(left_at__isnull=True, last_seen_at__lt=cutoff).update(left_at=timezone.now())


def _serialize_participant(p):
    return {
        'participant_id': p.pk,
        'user_id': p.user_id,
        'username': p.user.username,
        'avatar_color': p.user.avatar_color,
        'avatar_url': p.user.avatar_url,
        'joined_at': p.joined_at.isoformat(),
    }


def _serialize_group_call(call, active_participants):
    return {
        'call_id': call.pk,
        'group_id': call.group_id,
        'kind': call.kind,
        'started_by': call.started_by.username,
        'created_at': call.created_at.isoformat(),
        'participants': [_serialize_participant(p) for p in active_participants],
    }


@login_required
def group_call_state(request, group_id):
    """
    Polled from the group chat room so members see 'Join call' the moment
    someone starts one, and see the room empty out live as people leave —
    without needing an individual "ring" per member the way 1:1 calls do.
    """
    if not _group_membership_or_403(request.user, group_id):
        return JsonResponse({'error': 'not a member'}, status=403)

    call = GroupCall.objects.filter(group_id=group_id, ended_at__isnull=True).order_by('-created_at').first()
    if not call:
        return JsonResponse({'call': None})

    _reap_stale_participants(call)
    active = list(call.participants.filter(left_at__isnull=True).select_related('user').order_by('joined_at'))
    if not active:
        call.ended_at = timezone.now()
        call.save(update_fields=['ended_at'])
        return JsonResponse({'call': None})

    return JsonResponse({'call': _serialize_group_call(call, active)})


@login_required
@require_POST
def group_call_join(request, group_id):
    if not _group_membership_or_403(request.user, group_id):
        return JsonResponse({'error': 'not a member'}, status=403)

    call = GroupCall.objects.filter(group_id=group_id, ended_at__isnull=True).order_by('-created_at').first()

    if call:
        _reap_stale_participants(call)
    else:
        kind = request.POST.get('kind')
        if kind not in (GroupCall.KIND_AUDIO, GroupCall.KIND_VIDEO):
            return JsonResponse({'error': 'invalid kind'}, status=400)
        call = GroupCall.objects.create(group_id=group_id, kind=kind, started_by=request.user)

    existing = call.participants.filter(user=request.user, left_at__isnull=True).first()
    if existing:
        existing.last_seen_at = timezone.now()
        existing.save(update_fields=['last_seen_at'])
        me = existing
    else:
        active_count = call.participants.filter(left_at__isnull=True).count()
        if active_count >= MAX_GROUP_CALL_PARTICIPANTS:
            return JsonResponse({'error': 'call_full', 'max': MAX_GROUP_CALL_PARTICIPANTS}, status=409)
        me = GroupCallParticipant.objects.create(call=call, user=request.user)

    others = list(
        call.participants.filter(left_at__isnull=True)
        .exclude(pk=me.pk)
        .select_related('user')
        .order_by('joined_at')
    )
    payload = _serialize_group_call(call, [me] + others)
    payload['my_participant_id'] = me.pk
    # Mesh convention so both sides agree on who initiates without a race:
    # the lower user id always creates the offer for a given pair.
    payload['others'] = [
        dict(_serialize_participant(p), should_offer=request.user.pk < p.user_id)
        for p in others
    ]
    return JsonResponse(payload)


@login_required
@require_POST
def group_call_heartbeat(request, call_id):
    call = get_object_or_404(GroupCall, pk=call_id)
    participant = call.participants.filter(user=request.user, left_at__isnull=True).first()
    if not participant:
        return JsonResponse({'error': 'not in call'}, status=403)
    participant.last_seen_at = timezone.now()
    participant.save(update_fields=['last_seen_at'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def group_call_leave(request, call_id):
    call = get_object_or_404(GroupCall, pk=call_id)
    participant = call.participants.filter(user=request.user, left_at__isnull=True).first()
    if participant:
        participant.left_at = timezone.now()
        participant.save(update_fields=['left_at'])

    if not call.participants.filter(left_at__isnull=True).exists():
        call.ended_at = timezone.now()
        call.save(update_fields=['ended_at'])

    return JsonResponse({'ok': True})


@login_required
@require_POST
def group_call_signal_send(request, call_id):
    call = get_object_or_404(GroupCall, pk=call_id)
    if not call.participants.filter(user=request.user, left_at__isnull=True).exists():
        return JsonResponse({'error': 'not in call'}, status=403)

    to_username = request.POST.get('to_username', '')
    kind = request.POST.get('kind')
    sdp = request.POST.get('sdp', '')  # don't .strip() — see the offer_sdp note on Call
    if kind not in (GroupCallSignal.KIND_OFFER, GroupCallSignal.KIND_ANSWER):
        return JsonResponse({'error': 'invalid kind'}, status=400)
    if not sdp.strip():
        return JsonResponse({'error': 'missing sdp'}, status=400)

    to_user = get_object_or_404(User, username=to_username)
    if not call.participants.filter(user=to_user, left_at__isnull=True).exists():
        return JsonResponse({'error': 'recipient not in call'}, status=409)

    GroupCallSignal.objects.create(call=call, from_user=request.user, to_user=to_user, kind=kind, sdp=sdp)
    return JsonResponse({'ok': True})


@login_required
def group_call_signal_poll(request, call_id):
    call = get_object_or_404(GroupCall, pk=call_id)
    if not call.participants.filter(user=request.user, left_at__isnull=True).exists():
        return JsonResponse({'error': 'not in call'}, status=403)

    pending = list(
        GroupCallSignal.objects.filter(call=call, to_user=request.user, consumed=False)
        .select_related('from_user')
        .order_by('created_at')
    )
    ids = [s.pk for s in pending]
    if ids:
        GroupCallSignal.objects.filter(pk__in=ids).update(consumed=True)

    return JsonResponse({
        'signals': [
            {'from_username': s.from_user.username, 'kind': s.kind, 'sdp': s.sdp}
            for s in pending
        ]
    })
