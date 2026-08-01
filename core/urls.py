from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import include, path
from django.views.generic import RedirectView
from accounts import views as account_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(pattern_name='chat:inbox', permanent=False)),
    path('accounts/', include('accounts.urls')),
    path('spotify/callback/', account_views.spotify_callback, name='spotify_callback_root'),
    path('spotify/callback', account_views.spotify_callback),
    path('spotify/connect/', account_views.spotify_connect, name='spotify_connect_root'),
    path('spotify/connect', account_views.spotify_connect),
    path('friends/', include('friends.urls')),
    path('chat/', include('chat.urls')),
    path('calls/', include('calls.urls')),
    path('groups/', include('groupchat.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
