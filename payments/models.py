import uuid

from django.db import models

from orders.models import Order


class Payment(models.Model):
    """Платёж по заказу через шлюз TipTop Pay / SmartCore.

    Один заказ может иметь несколько Payment — если клиент повторяет неудачную
    оплату, создаётся новый Payment с новым invoice_id. Источник правды по
    статусу — подписанный callback от шлюза (см. payments/gateway.py).
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ожидает оплаты'
        SUCCEEDED = 'succeeded', 'Оплачен'
        FAILED = 'failed', 'Ошибка'
        REFUNDED = 'refunded', 'Возврат'

    order = models.ForeignKey(
        Order, verbose_name='Заказ', related_name='payments',
        on_delete=models.PROTECT,
    )
    amount = models.DecimalField('Сумма, ₸', max_digits=10, decimal_places=2)
    currency = models.CharField('Валюта', max_length=3, default='KZT')
    status = models.CharField(
        'Статус', max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    # Наш идентификатор платежа — уходит в шлюз как order_id и возвращается
    # в callback как orderId. По нему находим Payment при обработке уведомления.
    invoice_id = models.CharField('Наш ID платежа', max_length=64, unique=True)
    # ID платежа на стороне шлюза (order_id из ответа initPayment).
    external_id = models.CharField(
        'ID платежа в шлюзе', max_length=100, blank=True, unique=True, null=True,
    )
    # Ссылка на форму оплаты, выданная initPayment — по ней клиент платит.
    form_url = models.URLField('Ссылка на форму оплаты', max_length=500, blank=True)
    raw_callback_data = models.JSONField('Данные callback-уведомления', blank=True, null=True)

    paid_at = models.DateTimeField('Оплачен', null=True, blank=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлён', auto_now=True)

    @staticmethod
    def build_invoice_id(order):
        return f'bpf-{order.pk}-{uuid.uuid4().hex[:8]}'

    class Meta:
        verbose_name = 'Платёж'
        verbose_name_plural = 'Платежи'
        ordering = ['-created_at']

    def __str__(self):
        return f'Платёж по заказу №{self.order_id} — {self.get_status_display()}'
