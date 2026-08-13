from django.db import models

from catalog.models import Product


class Review(models.Model):
    """Отзыв клиента. Публикуется только после модерации."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Черновик'
        PUBLISHED = 'published', 'Опубликован'

    product = models.ForeignKey(
        Product, verbose_name='Товар', related_name='reviews',
        on_delete=models.CASCADE, null=True, blank=True,
    )
    author_name = models.CharField('Имя автора', max_length=150)
    text = models.TextField('Текст отзыва')
    rating = models.PositiveSmallIntegerField(
        'Оценка', choices=[(i, str(i)) for i in range(1, 6)], default=5,
    )
    status = models.CharField(
        'Статус', max_length=20, choices=Status.choices, default=Status.DRAFT,
    )
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'Отзыв'
        verbose_name_plural = 'Отзывы'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.author_name} — {self.rating}★'
