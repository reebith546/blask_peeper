from django.db import models


class DeliveryZone(models.Model):
    """Зона доставки (район города) с фиксированной ценой.

    Упрощённая MVP-версия: без расчёта расстояния по геокодингу (2GIS).
    Полноценный расчёт по радиусу от магазина — отдельная задача на будущее.
    """

    name = models.CharField('Район города', max_length=150)
    price = models.DecimalField('Цена доставки, ₸', max_digits=10, decimal_places=2)
    order = models.PositiveIntegerField('Порядок сортировки', default=0)
    is_active = models.BooleanField('Активна', default=True)

    class Meta:
        verbose_name = 'Зона доставки'
        verbose_name_plural = 'Зоны доставки'
        ordering = ['order', 'name']

    def __str__(self):
        return f'{self.name} — {self.price} ₸'
