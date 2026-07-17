from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from friends.models import Friendship
from . import metered
from .models import Call

User = get_user_model()


def _serialize(call, user):
    other = call.callee if call.caller_id == user.pk else call.caller
    return {
        'id': call.pk,
        'kind': call.kind,
        'status': call.status,
        'is_caller': call.caller_id == user.pk,
        'other_username': other.username,
        'other_avatar_color': other.avatar_color,
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

    offer_sdp = request.POST.get('offer_sdp', '').strip()
    if not offer_sdp:
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

    answer_sdp = request.POST.get('answer_sdp', '').strip()
    if not answer_sdp:
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
