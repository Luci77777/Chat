from django.urls import path
from . import views

app_name = 'friends'

urlpatterns = [
    path('search/', views.search_users, name='search'),
    path('requests/', views.friend_requests, name='requests'),
    path('list/', views.friend_list, name='list'),
    path('send/<str:username>/', views.send_request, name='send_request'),
    path('accept/<int:pk>/', views.accept_request, name='accept_request'),
    path('decline/<int:pk>/', views.decline_request, name='decline_request'),
    path('cancel/<int:pk>/', views.cancel_request, name='cancel_request'),
    path('remove/<str:username>/', views.remove_friend, name='remove_friend'),
]
