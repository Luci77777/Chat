"""
Thin client for the Klipy API (https://klipy.com) — a free GIF/sticker
search API. Tenor was shut down by Google on June 30, 2026 and GIPHY's
free tier is gone, so Klipy is the option that's actually free right now.

Schema below is verified against Klipy's own KlipyProvider.ts reference
implementation (gif-picker-react), not guessed from docs:

    GET https://api.klipy.com/api/v1/{APP_KEY}/{gifs|stickers}/search
        ?q=<query>&page=1&per_page=24&customer_id=<id>&content_filter=high

    { "result": true,
      "data": { "data": [ITEM, ...], "current_page": 1, "per_page": 24, "has_next": true } }

    ITEM = {
      "id": 123, "slug": "...", "title": "...", "type": "gif" | "ad",
      "tags": [...],
      "file": {
        "hd": {"gif": {"url", "width", "height", "size"}, "webp": {...}, "mp4": {...}, ...},
        "md": {...}, "sm": {...}, "xs": {...}
      }
    }
"""
import requests
from django.conf import settings

BASE_URL = 'https://api.klipy.com/api/v1/'
REQUEST_TIMEOUT = 5  # seconds — keep the picker feeling snappy, fail fast otherwise

# Prefer mid-size for the "send" image and small for the picker thumbnail,
# falling back to whatever sizes are actually present.
FULL_QUALITY_ORDER = ['md', 'hd', 'sm', 'xs']
PREVIEW_QUALITY_ORDER = ['sm', 'xs', 'md', 'hd']
FORMAT_ORDER = ['gif', 'webp', 'jpg', 'mp4', 'webm']


class KlipyError(Exception):
    pass


def is_configured():
    return bool(getattr(settings, 'KLIPY_API_KEY', ''))


def _pick_format(file_variant):
    if not file_variant:
        return None
    for fmt in FORMAT_ORDER:
        media = file_variant.get(fmt)
        if media and media.get('url'):
            return media
    return None


def _pick_quality(file_map, quality_order):
    if not file_map:
        return None
    for quality in quality_order:
        media = _pick_format(file_map.get(quality))
        if media:
            return media
    return None


def search(*, content_type, query, page, customer_id):
    """
    content_type: 'gif' or 'sticker'
    Returns (results, has_next). Raises KlipyError on failure so the view
    can decide how to respond (we treat this as non-fatal in the view).
    """
    if not is_configured():
        raise KlipyError('Klipy API key is not configured (set KLIPY_API_KEY).')

    path = 'stickers' if content_type == 'sticker' else 'gifs'
    endpoint = 'search' if query else 'trending'
    url = f'{BASE_URL}{settings.KLIPY_API_KEY}/{path}/{endpoint}'

    params = {
        'per_page': 24,
        'page': page,
        'customer_id': customer_id,
        'content_filter': 'high',
    }
    if query:
        params['q'] = query

    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise KlipyError(f'Klipy request failed: {exc}') from exc
    except ValueError as exc:
        raise KlipyError('Klipy returned an unparseable response') from exc

    if not payload.get('result'):
        raise KlipyError('Klipy returned result: false')

    data = payload.get('data') or {}
    items = data.get('data') or []

    results = []
    for item in items:
        if item.get('type') == 'ad':
            continue  # skip promotional slots — this app doesn't run Klipy's ad program

        file_map = item.get('file') or {}
        full = _pick_quality(file_map, FULL_QUALITY_ORDER)
        preview = _pick_quality(file_map, PREVIEW_QUALITY_ORDER)
        if not full or not preview:
            continue

        results.append({
            'id': item.get('slug') or str(item.get('id')),
            'title': item.get('title') or '',
            'url': full['url'],
            'preview_url': preview['url'],
            'width': preview.get('width'),
            'height': preview.get('height'),
        })

    return results, bool(data.get('has_next'))
