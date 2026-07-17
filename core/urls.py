from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(pattern_name='chat:inbox', permanent=False)),
    path('accounts/', include('accounts.urls')),
    path('friends/', include('friends.urls')),
    path('chat/', include('chat.urls')),
    path('calls/', include('calls.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
