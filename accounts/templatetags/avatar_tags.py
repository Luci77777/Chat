from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def avatar(entity, size=''):
    """
    Usage: {% avatar user %} or {% avatar group 'sm' %}

    Works for anything with a `.avatar_color` and either `.username` or
    `.name` — so it covers both User and ChatGroup objects. Renders an
    uploaded photo (avatar_url) when there is one, otherwise a colored
    letter avatar, so callers never need to branch on this themselves.

    Deliberately a `simple_tag` rather than an `inclusion_tag`: inclusion
    tags render via `context.new()`, which calls `copy.copy()` on the
    template Context — and that trips a Django/Python 3.14 incompatibility
    (BaseContext.__copy__ breaks under 3.14's stricter `super` objects).
    Building the HTML string directly here sidesteps that entirely.
    """
    url = getattr(entity, 'avatar_url', '') or ''
    color = getattr(entity, 'avatar_color', '#6C63FF') or '#6C63FF'
    label = getattr(entity, 'username', '') or getattr(entity, 'name', '') or '?'
    letter = label[:1].upper()
    css_class = f'avatar {size}'.strip()

    if url:
        return format_html('<img class="{}" src="{}" alt="{}">', css_class, url, letter)
    return format_html('<div class="{}" style="background:{};">{}</div>', css_class, color, letter)
