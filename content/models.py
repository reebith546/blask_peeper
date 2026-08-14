from django.db import models


class HomepageBlock(models.Model):
    """Редактируемый блок главной страницы (Hero, Instagram-галерея, промо).

    Сотрудники магазина сами управляют содержимым главной страницы через
    Django Admin: загружают фото, задают порядок и включают/выключают блоки.
    """

    class BlockType(models.TextChoices):
        HERO = 'hero', 'Hero-баннер'
        INSTAGRAM = 'instagram', 'Instagram-галерея'
        PROMO = 'promo', 'Промо-блок'

    block_type = models.CharField('Тип блока', max_length=20, choices=BlockType.choices)
    title = models.CharField('Заголовок', max_length=200, blank=True)
    subtitle = models.CharField('Подзаголовок', max_length=300, blank=True)
    image = models.ImageField('Изображение', upload_to='homepage/')
    link_url = models.CharField('Ссылка', max_length=300, blank=True)
    button_text = models.CharField('Текст кнопки', max_length=100, blank=True)
    order = models.PositiveIntegerField('Порядок сортировки', default=0)
    is_active = models.BooleanField('Показывать на сайте', default=True)

    class Meta:
        verbose_name = 'Блок главной страницы'
        verbose_name_plural = 'Блоки главной страницы'
        ordering = ['block_type', 'order']

    def __str__(self):
        return f'{self.get_block_type_display()} — {self.title or "без заголовка"}'


class NewsletterSubscriber(models.Model):
    """Подписчик формы «Новости и предложения» в футере."""

    email = models.EmailField('Email', unique=True)
    created_at = models.DateTimeField('Подписался', auto_now_add=True)

    class Meta:
        verbose_name = 'Подписчик рассылки'
        verbose_name_plural = 'Подписчики рассылки'
        ordering = ['-created_at']

    def __str__(self):
        return self.email
