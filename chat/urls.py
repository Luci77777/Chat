from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.inbox, name='inbox'),
    path('notify/summary/', views.notify_summary, name='notify_summary'),
    path('inbox/data/', views.inbox_data, name='inbox_data'),
    path('gif-search/', views.gif_search, name='gif_search'),
    path('with/<str:username>/', views.room, name='room'),
    path('with/<str:username>/poll/', views.poll_messages, name='poll'),
    path('with/<str:username>/send/', views.send_message, name='send'),
    path('with/<str:username>/typing/', views.typing, name='typing'),
    path('with/<str:username>/search/', views.search_messages, name='search'),
    path('with/<str:username>/mute/', views.toggle_mute, name='toggle_mute'),
    path('with/<str:username>/settings/', views.chat_settings, name='settings'),
    path('with/<str:username>/upload-voice/', views.upload_voice, name='upload_voice'),
    path('with/<str:username>/upload-file/', views.upload_file, name='upload_file'),
    path('message/<int:message_id>/edit/', views.edit_message, name='edit_message'),
    path('message/<int:message_id>/delete/', views.delete_message, name='delete_message'),
    path('message/<int:message_id>/react/', views.react_message, name='react_message'),
]
