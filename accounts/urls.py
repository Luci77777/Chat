from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('login/', auth_views.LoginView.as_view(template_name='accounts/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('profile/', views.profile, name='profile'),
    path('theme/', views.toggle_theme, name='toggle_theme'),
    path('spotify/connect/', views.spotify_connect, name='spotify_connect'),
    path('spotify/connect', views.spotify_connect),
    path('spotify/callback/', views.spotify_callback, name='spotify_callback'),
    path('spotify/callback', views.spotify_callback),
    path('spotify/disconnect/', views.spotify_disconnect, name='spotify_disconnect'),
    path('spotify/disconnect', views.spotify_disconnect),
]
