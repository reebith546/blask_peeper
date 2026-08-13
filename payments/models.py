from django.db import models

from orders.models import Order


class Payment(models.Model):
    """Платёж по заказу через TipTop Pay.

    Заготовка структуры данных для будущей интеграции: инициализация платежа
    и обработка callback-уведомлений с проверкой HMAC-подписи будут
    реализованы отдельным шагом (см. https://developers.tiptoppay.kz).
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
    # ID транзакции в TipTop Pay — используется для идемпотентной обработки
    # повторных callback-уведомлений (один external_id обрабатывается один раз).
    external_id = models.CharField(
        'ID транзакции TipTop Pay', max_length=100, blank=True, unique=True,
        null=True,
    )
    raw_callback_data = models.JSONField('Данные callback-уведомления', blank=True, null=True)

    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлён', auto_now=True)

    class Meta:
        verbose_name = 'Платёж'
        verbose_name_plural = 'Платежи'
        ordering = ['-created_at']

    def __str__(self):
        return f'Платёж по заказу №{self.order_id} — {self.get_status_display()}'
