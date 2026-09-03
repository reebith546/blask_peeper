from django.db import models

from catalog.models import Product
from delivery.models import DeliveryZone


class Order(models.Model):
    """Заказ. Оформляется гостем, без обязательной регистрации."""

    class Status(models.TextChoices):
        NEW = 'new', 'Новый'
        PENDING_PAYMENT = 'pending_payment', 'Ожидает оплаты'
        PAID = 'paid', 'Оплачен'
        PROCESSING = 'processing', 'Собирается'
        DELIVERED = 'delivered', 'Доставлен'
        PAYMENT_FAILED = 'payment_failed', 'Ошибка оплаты'

    status = models.CharField(
        'Статус', max_length=20, choices=Status.choices, default=Status.NEW,
    )

    # Отправитель (заказчик) — тот, кто оформляет заказ, ему звонит менеджер.
    customer_name = models.CharField('Имя отправителя', max_length=150)
    customer_phone = models.CharField('Телефон отправителя', max_length=32)

    # Получатель — кому вручают букет (может совпадать с отправителем).
    recipient_name = models.CharField('Имя получателя', max_length=150, blank=True)
    recipient_phone = models.CharField('Телефон получателя', max_length=32, blank=True)

    # Доставка. Координаты адреса намеренно не хранятся — бесплатный тариф
    # Яндекс Карт запрещает сохранять данные геокодирования, координаты
    # используются только на лету для расчёта зоны/цены в момент заказа.
    delivery_zone = models.ForeignKey(
        DeliveryZone, verbose_name='Зона доставки', related_name='orders',
        on_delete=models.PROTECT, null=True, blank=True,
    )
    delivery_address = models.CharField('Адрес доставки', max_length=300)
    delivery_date = models.DateField('Дата доставки', null=True, blank=True)
    delivery_time = models.CharField(
        'Время доставки', max_length=50, blank=True,
        help_text='Например: «14:00–16:00»',
    )
    delivery_price = models.DecimalField('Стоимость доставки, ₸', max_digits=10, decimal_places=2, default=0)

    # Открытка (опционально, заполняется в корзине)
    card_text = models.TextField('Текст открытки', blank=True)

    comment = models.TextField('Комментарий к заказу', blank=True)

    total_price = models.DecimalField('Итоговая сумма, ₸', max_digits=10, decimal_places=2, default=0)

    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлён', auto_now=True)

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        ordering = ['-created_at']

    def __str__(self):
        return f'Заказ №{self.pk} ({self.get_status_display()})'


class OrderItem(models.Model):
    """Позиция в заказе — товар со снапшотом цены на момент заказа."""

    order = models.ForeignKey(
        Order, verbose_name='Заказ', related_name='items',
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        Product, verbose_name='Товар', related_name='order_items',
        on_delete=models.PROTECT,
    )
    quantity = models.PositiveIntegerField('Количество', default=1)
    price = models.DecimalField(
        'Цена на момент заказа, ₸', max_digits=10, decimal_places=2,
    )

    class Meta:
        verbose_name = 'Позиция заказа'
        verbose_name_plural = 'Позиции заказа'

    def __str__(self):
        return f'{self.product.name} x{self.quantity}'

    @property
    def subtotal(self):
        return self.price * self.quantity
