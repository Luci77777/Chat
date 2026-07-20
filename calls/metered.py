"""
Fetches short-lived TURN credentials from Metered / Open Relay
(https://www.metered.ca/stun-turn) — the free tier that's actually still
free right now (Tenor-style "just works forever" free TURN doesn't really
exist; Metered's free plan is 0.5GB/month with no card, 20GB/month with a
card added, no overage charges either way — plenty for a personal app's
voice/video calls).

TURN is only needed as a fallback for the ~10-15% of connections where two
browsers can't reach each other directly (symmetric NAT, strict corporate
firewalls). Without a Metered key configured, calls still work for most
people via free public STUN servers alone — they just won't connect for
that harder-network minority. This is documented in the README.
"""
import requests
from django.conf import settings

REQUEST_TIMEOUT = 5

# Always-available, no-signup-required STUN servers. These just help two
# browsers discover their public IP/port — they never see call media.
PUBLIC_STUN_SERVERS = [
    {'urls': 'stun:stun.l.google.com:19302'},
    {'urls': 'stun:stun1.l.google.com:19302'},
    {'urls': 'stun:stun.cloudflare.com:3478'},
]


def is_turn_configured():
    return bool(getattr(settings, 'METERED_APP_DOMAIN', '') and getattr(settings, 'METERED_API_KEY', ''))


def _normalize_ice_server(entry):
    """
    RTCPeerConnection throws synchronously if any entry in `iceServers`
    doesn't have a valid `urls` field — and since that constructor call
    happens deep inside the call-setup flow, an unexpected shape from a
    third-party API can silently kill the whole call with no visible error.
    Some TURN providers (and older Metered/Twilio-style responses) use the
    singular `url` key instead of `urls`; normalize that, and drop anything
    that still doesn't have a usable URL.
    """
    if not isinstance(entry, dict):
        return None

    urls = entry.get('urls') or entry.get('url')
    if not urls or not isinstance(urls, (str, list)):
        return None

    normalized = {'urls': urls}
    if entry.get('username'):
        normalized['username'] = entry['username']
    if entry.get('credential'):
        normalized['credential'] = entry['credential']
    return normalized


def get_ice_servers():
    """
    Returns a list of RTCIceServer dicts: our free STUN servers, plus (if
    configured) fresh TURN credentials from Metered. Never raises — TURN
    fetch failures just mean the caller falls back to STUN-only.
    """
    servers = list(PUBLIC_STUN_SERVERS)

    if not is_turn_configured():
        return servers

    domain = settings.METERED_APP_DOMAIN
    api_key = settings.METERED_API_KEY
    url = f'https://{domain}/api/v1/turn/credentials'

    try:
        resp = requests.get(url, params={'apiKey': api_key}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        turn_servers = resp.json()
        if isinstance(turn_servers, list):
            for entry in turn_servers:
                normalized = _normalize_ice_server(entry)
                if normalized:
                    servers.append(normalized)
    except Exception:
        # Degrade to STUN-only rather than break call setup — this must
        # never raise, since createPeerConnection() awaits this endpoint
        # on every single call attempt.
        pass

    return servers