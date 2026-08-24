"""Настройка главной страницы Django Admin: карточки с быстрой сводкой.

Подключается из core/urls.py (после того как приложения полностью готовы),
чтобы модели можно было безопасно импортировать внутри функции.
"""
import types

from django.contrib import admin
from django.urls import reverse


def _dashboard_stats(request):
    from catalog.models import Product
    from content.models import NewsletterSubscriber
    from django.utils import timezone
    from datetime import timedelta
    from orders.models import Order
    from reviews.models import Review

    stats = [
        {
            'label': 'Новых заказов',
            'value': Order.objects.filter(status=Order.Status.NEW).count(),
            'url': reverse('admin:orders_order_changelist') + '?status__exact=new',
            'tone': 'warning',
        },
        {
            'label': 'Товаров без остатка',
            'value': Product.objects.filter(in_stock=False, is_active=True).count(),
            'url': reverse('admin:catalog_product_changelist') + '?in_stock__exact=0',
            'tone': 'warning',
        },
        {
            'label': 'Отзывов на модерации',
            'value': Review.objects.filter(status=Review.Status.DRAFT).count(),
            'url': reverse('admin:reviews_review_changelist') + '?status__exact=draft',
            'tone': 'default',
        },
        {
            'label': 'Подписчиков за неделю',
            'value': NewsletterSubscriber.objects.filter(
                created_at__gte=timezone.now() - timedelta(days=7),
            ).count(),
            'url': reverse('admin:content_newslettersubscriber_changelist'),
            'tone': 'default',
        },
    ]
    # Только у моделей, которые пользователь реально может открыть.
    return [
        stat for stat in stats
        if request.user.has_perm(_perm_for_url(stat['url']))
    ]


def _perm_for_url(url):
    # '/admin/orders/order/?status__exact=new' -> 'orders.view_order'
    parts = url.split('?')[0].strip('/').split('/')
    app_label, model_name = parts[1], parts[2]
    return f'{app_label}.view_{model_name}'


def _index_with_stats(self, request, extra_context=None):
    extra_context = extra_context or {}
    extra_context['dashboard_stats'] = _dashboard_stats(request)
    return admin.AdminSite.index(self, request, extra_context)


def install():
    admin.site.index = types.MethodType(_index_with_stats, admin.site)
