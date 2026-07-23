from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from . import cloudinary_client
from .forms import ProfileForm, SignUpForm


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
                    return render(request, 'accounts/profile.html', {'form': form})
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
    })
