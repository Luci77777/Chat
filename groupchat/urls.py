from django.urls import path
from . import views

app_name = 'groupchat'

urlpatterns = [
    path('', views.group_list, name='list'),
    path('new/', views.group_create, name='create'),
    path('<int:group_id>/', views.group_room, name='room'),
    path('<int:group_id>/poll/', views.group_poll, name='poll'),
    path('<int:group_id>/send/', views.group_send, name='send'),
    path('<int:group_id>/typing/', views.group_typing, name='typing'),
    path('<int:group_id>/search/', views.group_search_messages, name='search'),
    path('<int:group_id>/mute/', views.group_toggle_mute, name='toggle_mute'),
    path('<int:group_id>/upload-voice/', views.group_upload_voice, name='upload_voice'),
    path('<int:group_id>/upload-file/', views.group_upload_file, name='upload_file'),
    path('<int:group_id>/gif-search/', views.group_gif_search, name='gif_search'),
    path('<int:group_id>/add/', views.group_add_members, name='add_members'),
    path('<int:group_id>/settings/', views.group_settings, name='settings'),
    path('<int:group_id>/remove/<int:user_id>/', views.group_remove_member, name='remove_member'),
    path('<int:group_id>/set-admin/<int:user_id>/', views.group_set_admin, name='set_admin'),
    path('<int:group_id>/leave/', views.group_leave, name='leave'),
    path('message/<int:message_id>/edit/', views.group_edit_message, name='edit_message'),
    path('message/<int:message_id>/delete/', views.group_delete_message, name='delete_message'),
    path('message/<int:message_id>/react/', views.group_react_message, name='react_message'),
]
