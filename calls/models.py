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
