from django.urls import path

from . import views

app_name = 'payments'

urlpatterns = [
    # URL для личного кабинета шлюза: https://<домен>/payments/callback/
    path('callback/', views.payment_callback, name='callback'),
    path('order/<int:order_id>/failed/', views.payment_failed, name='failed'),
    path('order/<int:order_id>/retry/', views.payment_retry, name='retry'),
]
