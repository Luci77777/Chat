from django.urls import path
from . import views

app_name = 'calls'

urlpatterns = [
    path('ice-servers/', views.ice_servers, name='ice_servers'),
    path('incoming/', views.incoming_call, name='incoming'),
    path('start/<str:username>/', views.start_call, name='start'),
    path('<int:call_id>/status/', views.call_status, name='status'),
    path('<int:call_id>/accept/', views.accept_call, name='accept'),
    path('<int:call_id>/decline/', views.decline_call, name='decline'),
    path('<int:call_id>/end/', views.end_call, name='end'),

    # Group calls (mesh)
    path('group/<int:group_id>/state/', views.group_call_state, name='group_state'),
    path('group/<int:group_id>/join/', views.group_call_join, name='group_join'),
    path('group/<int:call_id>/heartbeat/', views.group_call_heartbeat, name='group_heartbeat'),
    path('group/<int:call_id>/leave/', views.group_call_leave, name='group_leave'),
    path('group/<int:call_id>/signal/send/', views.group_call_signal_send, name='group_signal_send'),
    path('group/<int:call_id>/signal/poll/', views.group_call_signal_poll, name='group_signal_poll'),
]
