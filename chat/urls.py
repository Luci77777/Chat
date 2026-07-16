from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.inbox, name='inbox'),
    path('notify/summary/', views.notify_summary, name='notify_summary'),
    path('inbox/data/', views.inbox_data, name='inbox_data'),
    path('with/<str:username>/', views.room, name='room'),
    path('with/<str:username>/poll/', views.poll_messages, name='poll'),
    path('with/<str:username>/send/', views.send_message, name='send'),
]
