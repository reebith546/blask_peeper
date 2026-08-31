from django.urls import path

from . import views

app_name = 'payments'

urlpatterns = [
    # Webhook TipTop Pay. В ЛК укажите с типом события в query:
    #   https://<домен>/payments/callback/?type=pay | ?type=fail | ?type=refund
    path('callback/', views.payment_callback, name='callback'),
    path('order/<int:order_id>/failed/', views.payment_failed, name='failed'),
    path('order/<int:order_id>/retry/', views.payment_retry, name='retry'),
]
