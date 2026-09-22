"""Настройка главной страницы Django Admin: короткая сводка «что требует внимания».

Подключается из core/urls.py (после того как приложения полностью готовы),
чтобы модели можно было безопасно импортировать внутри функции.

Принцип: на дашборде показываем только показатели, по которым СЕЙЧАС есть
работа (значение > 0). Нулевые счётчики не выводим — иначе они приучают
не смотреть на сводку вообще. Если работы нет — явное «всё спокойно».
"""
import logging
import types

from django.contrib import admin
from django.urls import reverse

logger = logging.getLogger('payments')

# Сколько зависших платежей максимум опрашивать за один заход в админку —
# чтобы недоступный шлюз не превратил открытие дашборда в долгое ожидание.
_MAX_AUTO_RECHECK = 20


def _sync_todays_pending_payments(request):
    """Автоматически опрашивает TipTop Pay по сегодняшним платежам, которые
    всё ещё висят в «Ожидает оплаты» — то же самое, что действие «Проверить
    статус в платёжном шлюзе» в Платежах, но без ручного захода туда.
    Источник правды по оплате — подписанный webhook, это лишь подстраховка
    на случай, если он не настроен в кабинете шлюза или потерялся по сети."""
    if not request.user.has_perm('payments.change_payment'):
        return

    from django.utils import timezone

    from payments import gateway
    from payments.models import Payment

    if not gateway.payments_enabled():
        return

    today = timezone.localdate()
    pending = (
        Payment.objects
        .filter(status=Payment.Status.PENDING, created_at__date=today)
        .order_by('-created_at')[:_MAX_AUTO_RECHECK]
    )
    for payment in pending:
        try:
            gateway.check_payment(payment, source='admin_dashboard')
        except gateway.PaymentGatewayError:
            logger.exception('автопроверка на дашборде: не удалось опросить статус платежа %s', payment.invoice_id)


def _dashboard_stats(request):
    from audit.models import AuditEvent
    from catalog.models import Product
    from django.utils import timezone
    from datetime import timedelta
    from orders.models import Order
    from reviews.models import Review

    day_ago = timezone.now() - timedelta(hours=24)

    # Кандидаты «требует внимания». Показываем те, где value > 0 и есть доступ.
    candidates = [
        {
            'label': 'Новых заказов',
            'value': Order.objects.filter(status=Order.Status.NEW).count(),
            'url': reverse('admin:orders_order_changelist') + '?status__exact=new',
        },
        {
            'label': 'Товаров без остатка',
            'value': Product.objects.filter(in_stock=False, is_active=True).count(),
            'url': reverse('admin:catalog_product_changelist') + '?in_stock__exact=0',
        },
        {
            'label': 'Отзывов на модерации',
            'value': Review.objects.filter(status=Review.Status.DRAFT).count(),
            'url': reverse('admin:reviews_review_changelist') + '?status__exact=draft',
        },
        {
            'label': 'Неудачных входов за сутки',
            'value': AuditEvent.objects.filter(
                action=AuditEvent.Action.LOGIN_FAILED, timestamp__gte=day_ago,
            ).count(),
            'url': reverse('admin:audit_auditevent_changelist') + '?action__exact=login_failed',
        },
    ]
    return [
        dict(stat, tone='warning')
        for stat in candidates
        if stat['value'] > 0 and request.user.has_perm(_perm_for_url(stat['url']))
    ]


def _has_dashboard_scope(request):
    """Есть ли у пользователя вообще раздел, за которым он следит на дашборде."""
    perms = ('orders.view_order', 'catalog.view_product', 'reviews.view_review')
    return any(request.user.has_perm(p) for p in perms)


def _perm_for_url(url):
    # '/admin/orders/order/?status__exact=new' -> 'orders.view_order'
    parts = url.split('?')[0].strip('/').split('/')
    app_label, model_name = parts[1], parts[2]
    return f'{app_label}.view_{model_name}'


def _index_with_stats(self, request, extra_context=None):
    extra_context = extra_context or {}
    _sync_todays_pending_payments(request)
    stats = _dashboard_stats(request)
    extra_context['dashboard_stats'] = stats
    extra_context['dashboard_calm'] = not stats and _has_dashboard_scope(request)
    return admin.AdminSite.index(self, request, extra_context)


def install():
    admin.site.index = types.MethodType(_index_with_stats, admin.site)
