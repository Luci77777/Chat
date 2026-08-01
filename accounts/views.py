import secrets

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import cloudinary_client, spotify_client
from .forms import ProfileForm, SignUpForm
from .models import User


def signup(request):
    if request.user.is_authenticated:
        return redirect('chat:inbox')
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Welcome, {user.username}! Your account is ready.')
            return redirect('chat:inbox')
    else:
        form = SignUpForm()
    return render(request, 'accounts/signup.html', {'form': form})


@login_required
def profile(request):
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            user = form.save(commit=False)

            avatar_file = form.cleaned_data.get('avatar')
            remove_avatar = form.cleaned_data.get('remove_avatar')

            if avatar_file:
                try:
                    url, public_id = cloudinary_client.upload_avatar(avatar_file, request.user)
                except cloudinary_client.CloudinaryError as exc:
                    messages.error(request, f"Couldn't upload that photo: {exc}")
                    return render(request, 'accounts/profile.html', {
                        'form': form, 'cloudinary_configured': cloudinary_client.is_configured(),
                    })
                old_public_id = request.user.avatar_public_id
                user.avatar_url = url
                user.avatar_public_id = public_id
                if old_public_id and old_public_id != public_id:
                    cloudinary_client.delete_avatar(old_public_id)
            elif remove_avatar and user.avatar_url:
                cloudinary_client.delete_avatar(user.avatar_public_id)
                user.avatar_url = ''
                user.avatar_public_id = ''

            user.save()
            messages.success(request, 'Profile updated.')
            return redirect('accounts:profile')
    else:
        form = ProfileForm(instance=request.user)
    return render(request, 'accounts/profile.html', {
        'form': form,
        'cloudinary_configured': cloudinary_client.is_configured(),
        'spotify_configured': spotify_client.is_configured(),
        'now_playing': spotify_client.get_now_playing_cached(request.user) if spotify_client.is_configured() else None,
    })


@login_required
@require_POST
def toggle_theme(request):
    new_theme = User.THEME_DARK if request.user.theme_preference == User.THEME_LIGHT else User.THEME_LIGHT
    User.objects.filter(pk=request.user.pk).update(theme_preference=new_theme)
    return JsonResponse({'theme': new_theme})


@login_required
def spotify_connect(request):
    if not spotify_client.is_configured():
        messages.error(request, 'Spotify is not configured on this server.')
        return redirect('accounts:profile')
    state = secrets.token_urlsafe(24)
    request.session['spotify_oauth_state'] = state
    return redirect(spotify_client.build_authorize_url(state))


@login_required
def spotify_callback(request):
    if not spotify_client.is_configured():
        messages.error(request, 'Spotify is not configured on this server.')
        return redirect('accounts:profile')

    error = request.GET.get('error')
    if error:
        messages.info(request, 'Spotify connection cancelled.')
        return redirect('accounts:profile')

    expected_state = request.session.pop('spotify_oauth_state', None)
    state = request.GET.get('state')
    if not expected_state or state != expected_state:
        messages.error(request, "Couldn't verify that Spotify request — please try connecting again.")
        return redirect('accounts:profile')

    code = request.GET.get('code')
    if not code:
        messages.error(request, 'Spotify did not return an authorization code.')
        return redirect('accounts:profile')

    try:
        data = spotify_client.exchange_code_for_tokens(code)
    except spotify_client.SpotifyError as exc:
        messages.error(request, f"Couldn't connect Spotify: {exc}")
        return redirect('accounts:profile')

    request.user.spotify_access_token = data['access_token']
    request.user.spotify_refresh_token = data.get('refresh_token', request.user.spotify_refresh_token)
    request.user.spotify_token_expires_at = timezone.now() + timezone.timedelta(seconds=data.get('expires_in', 3600))
    request.user.save(update_fields=['spotify_access_token', 'spotify_refresh_token', 'spotify_token_expires_at'])

    messages.success(request, 'Spotify connected — your now-playing will show up next to your name.')
    return redirect('accounts:profile')


@login_required
@require_POST
def spotify_disconnect(request):
    User.objects.filter(pk=request.user.pk).update(
        spotify_access_token='', spotify_refresh_token='', spotify_token_expires_at=None,
    )
    from .models import SpotifyNowPlaying
    SpotifyNowPlaying.objects.filter(user=request.user).delete()
    messages.success(request, 'Spotify disconnected.')
    return redirect('accounts:profile')
