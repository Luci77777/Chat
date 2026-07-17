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
]
