from django import template

register = template.Library()


@register.inclusion_tag('partials/_avatar.html')
def avatar(entity, size=''):
    """
    Usage: {% avatar user %} or {% avatar group 'sm' %}

    Works for anything with a `.avatar_color` and either `.username` or
    `.name` — so it covers both User and ChatGroup objects. Renders an
    uploaded photo (avatar_url) when there is one, otherwise a colored
    letter avatar, so callers never need to branch on this themselves.
    """
    url = getattr(entity, 'avatar_url', '') or ''
    color = getattr(entity, 'avatar_color', '#6C63FF') or '#6C63FF'
    label = getattr(entity, 'username', '') or getattr(entity, 'name', '') or '?'
    return {'url': url, 'color': color, 'letter': label[:1].upper(), 'size': size}
