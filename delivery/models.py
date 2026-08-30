from django.db import models


class ShopLocation(models.Model):
    """Координаты магазина — точка отсчёта для расчёта зон доставки по радиусу."""

    name = models.CharField('Название точки', max_length=150, default='Black Pepper Flower Bar')
    address = models.CharField('Адрес', max_length=300, blank=True)
    latitude = models.DecimalField('Широта', max_digits=9, decimal_places=6)
    longitude = models.DecimalField('Долгота', max_digits=9, decimal_places=6)

    class Meta:
        verbose_name = 'Точка магазина'
        verbose_name_plural = 'Точки магазина'

    def __str__(self):
        return self.name


class DeliveryZone(models.Model):
    """Зона доставки — кольцо расстояний от магазина (в км), с ценой и сроком.

    Расстояние до клиента считается по координатам (формула Haversine) в
    delivery/services.py. Пока не подключён геокодинг на чекауте, зону также
    можно выбрать вручную — это осознанный резервный сценарий, а не баг.
    """

    name = models.CharField('Название зоны', max_length=150)
    radius_from_km = models.DecimalField('От, км', max_digits=7, decimal_places=2, default=0)
    radius_to_km = models.DecimalField('До, км', max_digits=7, decimal_places=2, default=10)
    price = models.DecimalField(
        'Цена доставки, ₸', max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Не заполняется, если доставка по согласованию с менеджером.',
    )
    price_on_request = models.BooleanField(
        'Доставка по согласованию', default=False,
        help_text='Фиксированной цены нет: заказ уходит менеджеру, он согласует стоимость '
                  '(например, для дальних адресов — за городом).',
    )
    delivery_time_minutes = models.PositiveIntegerField('Время доставки, мин', default=60)
    order = models.PositiveIntegerField('Порядок сортировки', default=0)
    is_active = models.BooleanField('Активна', default=True)

    class Meta:
        verbose_name = 'Зона доставки'
        verbose_name_plural = 'Зоны доставки'
        ordering = ['radius_from_km', 'order']

    def __str__(self):
        tail = 'по согласованию' if self.price_on_request else f'{self.price} ₸'
        return f'{self.name} ({self.radius_from_km}–{self.radius_to_km} км) — {tail}'

    def clean(self):
        from django.core.exceptions import ValidationError

        if not self.price_on_request and self.price is None:
            raise ValidationError({'price': 'Укажите цену или отметьте «Доставка по согласованию».'})
