from django.urls import path

from . import views

app_name = 'main'

urlpatterns = [
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('offer/', views.offer, name='offer'),
    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),
    path('cart/', views.cart_detail, name='cart'),
    path('cart/add/<int:product_id>/', views.cart_add, name='cart_add'),
    path('cart/update/<int:product_id>/', views.cart_update, name='cart_update'),
    path('cart/remove/<int:product_id>/', views.cart_remove, name='cart_remove'),
    path('cart/details/', views.cart_details, name='cart_details'),
    path('checkout/', views.checkout, name='checkout'),
    path('checkout/address-suggest/', views.address_suggest_ajax, name='address_suggest'),
    path('checkout/address-resolve/', views.address_resolve_ajax, name='address_resolve'),
    path('order/<int:order_id>/success/', views.order_success, name='order_success'),
]
