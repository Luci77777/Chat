from django.conf import settings
from django.db import models


class Call(models.Model):
    KIND_AUDIO = 'audio'
    KIND_VIDEO = 'video'
    KIND_CHOICES = [(KIND_AUDIO, 'Audio'), (KIND_VIDEO, 'Video')]

    STATUS_RINGING = 'ringing'
    STATUS_ACCEPTED = 'accepted'
    STATUS_DECLINED = 'declined'
    STATUS_ENDED = 'ended'
    STATUS_MISSED = 'missed'
    STATUS_CHOICES = [
        (STATUS_RINGING, 'Ringing'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_DECLINED, 'Declined'),
        (STATUS_ENDED, 'Ended'),
        (STATUS_MISSED, 'Missed'),
    ]

    ACTIVE_STATUSES = (STATUS_RINGING, STATUS_ACCEPTED)

    caller = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='outgoing_calls', on_delete=models.CASCADE)
    callee = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='incoming_calls', on_delete=models.CASCADE)
    kind = models.CharField(max_length=5, choices=KIND_CHOICES, default=KIND_AUDIO)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_RINGING)

    # WebRTC signaling payloads. We use "non-trickle" ICE (the browser waits
    # for ICE gathering to finish before sending its SDP) specifically so we
    # don't need a live transport for signaling — plain HTTP polling is
    # enough, which keeps this deployable on any free host, Render included.
    offer_sdp = models.TextField(blank=True)
    answer_sdp = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # Powers the cheap "do I have an incoming call" check that runs
            # on every notify_summary poll (every ~12s, from every page).
            models.Index(fields=['callee', 'status'], name='calls_call_callee_status_idx'),
            models.Index(fields=['caller', 'status'], name='calls_call_caller_status_idx'),
        ]

    def __str__(self):
        return f'{self.kind} call {self.caller} -> {self.callee} ({self.status})'


class GroupCall(models.Model):
    """
    A group-call "room" tied to a group chat. Unlike the 1:1 Call above,
    nobody "rings" individually — members join an active room the same way
    they'd join a meeting link. Under the hood this is still a mesh of
    ordinary pairwise WebRTC connections (one per pair of participants),
    signaled the same non-trickle, HTTP-polling way as 1:1 calls — see
    GroupCallSignal below — which is why group size is capped (see
    MAX_PARTICIPANTS in calls/views.py): mesh connections grow as N*(N-1)/2,
    so this is meant for small groups, not large broadcast calls.
    """
    KIND_AUDIO = 'audio'
    KIND_VIDEO = 'video'
    KIND_CHOICES = [(KIND_AUDIO, 'Audio'), (KIND_VIDEO, 'Video')]

    group = models.ForeignKey('groupchat.ChatGroup', related_name='calls', on_delete=models.CASCADE)
    kind = models.CharField(max_length=5, choices=KIND_CHOICES, default=KIND_AUDIO)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_active(self):
        return self.ended_at is None

    def __str__(self):
        return f'group call in {self.group} ({"active" if self.is_active else "ended"})'


class GroupCallParticipant(models.Model):
    call = models.ForeignKey(GroupCall, related_name='participants', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    # Heartbeat so we can detect someone whose tab died without a clean
    # "leave" call — see STALE_AFTER_SECONDS in calls/views.py.
    last_seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['call', 'left_at'], name='calls_gcp_call_left_idx'),
        ]

    @property
    def is_active(self):
        return self.left_at is None

    def __str__(self):
        return f'{self.user} in call#{self.call_id}'


class GroupCallSignal(models.Model):
    """
    One pairwise WebRTC offer/answer envelope within a group call's mesh.
    Each of the N*(N-1) directed pairs in the mesh exchanges exactly one
    offer and one answer here, mirroring the same non-trickle SDP exchange
    the 1:1 Call model uses — just addressed by (from_user, to_user)
    instead of being a fixed caller/callee pair, so it scales to N legs
    without needing a new DB table per pair.
    """
    KIND_OFFER = 'offer'
    KIND_ANSWER = 'answer'
    KIND_CHOICES = [(KIND_OFFER, 'Offer'), (KIND_ANSWER, 'Answer')]

    call = models.ForeignKey(GroupCall, related_name='signals', on_delete=models.CASCADE)
    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    sdp = models.TextField()  # see the offer_sdp note on Call above re: not stripping this
    created_at = models.DateTimeField(auto_now_add=True)
    consumed = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['call', 'to_user', 'consumed'], name='calls_gcs_call_to_consumed_idx'),
        ]

    def __str__(self):
        return f'{self.kind} {self.from_user}->{self.to_user} (call#{self.call_id})'
