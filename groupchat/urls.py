from django.urls import path
from . import views

app_name = 'groupchat'

urlpatterns = [
    path('', views.group_list, name='list'),
    path('new/', views.group_create, name='create'),
    path('<int:group_id>/', views.group_room, name='room'),
    path('<int:group_id>/poll/', views.group_poll, name='poll'),
    path('<int:group_id>/send/', views.group_send, name='send'),
    path('<int:group_id>/gif-search/', views.group_gif_search, name='gif_search'),
    path('<int:group_id>/add/', views.group_add_members, name='add_members'),
    path('<int:group_id>/leave/', views.group_leave, name='leave'),
]
