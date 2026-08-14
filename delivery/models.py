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
    radius_from_km = models.DecimalField('От, км', max_digits=5, decimal_places=2, default=0)
    radius_to_km = models.DecimalField('До, км', max_digits=5, decimal_places=2, default=10)
    price = models.DecimalField('Цена доставки, ₸', max_digits=10, decimal_places=2)
    delivery_time_minutes = models.PositiveIntegerField('Время доставки, мин', default=60)
    order = models.PositiveIntegerField('Порядок сортировки', default=0)
    is_active = models.BooleanField('Активна', default=True)

    class Meta:
        verbose_name = 'Зона доставки'
        verbose_name_plural = 'Зоны доставки'
        ordering = ['radius_from_km', 'order']

    def __str__(self):
        return f'{self.name} ({self.radius_from_km}–{self.radius_to_km} км) — {self.price} ₸'
