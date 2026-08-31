import uuid

from django.db import models

from orders.models import Order

DEFAULT_API_BASE = 'https://api.tiptoppay.kz'


class PaymentSettings(models.Model):
    """Реквизиты TipTop Pay, редактируемые владельцем в админке.

    Синглтон (всегда одна запись, pk=1). Если заполнены Public ID + API Secret
    и включён флаг — онлайн-оплата работает с этими значениями. Значения из
    .env используются только как запасной вариант, когда запись не заполнена
    (см. payments/gateway.get_config).

    У TipTop Pay всего два реквизита — Public ID и API Secret (он же пароль API
    и ключ подписи webhook-уведомлений). Авторизация — HTTP Basic
    base64(PublicId:ApiSecret).
    """

    is_enabled = models.BooleanField(
        'Принимать онлайн-оплату', default=False,
        help_text='Выключите, чтобы временно вернуться к оплате через менеджера.',
    )
    public_id = models.CharField(
        'Public ID', max_length=200, blank=True,
        help_text='Из личного кабинета TipTop Pay, вида «pk_…».',
    )
    api_secret = models.CharField(
        'API Secret (пароль API и ключ подписи)', max_length=255, blank=True,
        help_text='Хранится в базе. Не показывается после сохранения.',
    )
    api_base = models.URLField(
        'Адрес API', max_length=200, blank=True, default=DEFAULT_API_BASE,
        help_text='Меняйте, только если у вашего аккаунта другой домен API.',
    )
    currency = models.CharField('Валюта', max_length=3, default='KZT')
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Настройки онлайн-оплаты'
        verbose_name_plural = 'Настройки онлайн-оплаты'

    def __str__(self):
        return 'Настройки онлайн-оплаты'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_ready(self):
        return bool(self.is_enabled and self.public_id and self.api_secret)


class Payment(models.Model):
    """Платёж по заказу через TipTop Pay.

    Один заказ может иметь несколько Payment — если клиент повторяет неудачную
    оплату, создаётся новый Payment с новым invoice_id. Источник правды по
    статусу — подписанное webhook-уведомление от шлюза (см. payments/gateway.py).
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
    # ID счёта на стороне шлюза (Model.Id из ответа /orders/create).
    external_id = models.CharField(
        'ID счёта в шлюзе', max_length=100, blank=True, unique=True, null=True,
    )
    # Ссылка на форму оплаты (Model.Url из /orders/create) — по ней клиент платит.
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
