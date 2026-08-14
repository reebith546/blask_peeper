from django.db import models
from django.utils.text import slugify


class Category(models.Model):
    """Категория товаров каталога (например, «Монобукеты», «Свадебные»)."""

    name = models.CharField('Название', max_length=150)
    slug = models.SlugField('Слаг (для URL)', max_length=160, unique=True, blank=True, allow_unicode=True)
    image = models.ImageField('Изображение', upload_to='categories/', blank=True, null=True)
    order = models.PositiveIntegerField('Порядок сортировки', default=0)
    is_active = models.BooleanField('Активна', default=True)

    class Meta:
        verbose_name = 'Категория'
        verbose_name_plural = 'Категории'
        ordering = ['order', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)


class Product(models.Model):
    """Товар (букет/композиция)."""

    category = models.ForeignKey(
        Category, verbose_name='Категория', related_name='products',
        on_delete=models.PROTECT,
    )
    name = models.CharField('Название', max_length=200)
    slug = models.SlugField('Слаг (для URL)', max_length=210, unique=True, blank=True, allow_unicode=True)
    price = models.DecimalField('Цена, ₸', max_digits=10, decimal_places=2)
    composition = models.TextField('Состав', blank=True)
    description = models.TextField('Описание', blank=True)
    image = models.ImageField('Главное фото', upload_to='products/')
    stock = models.PositiveIntegerField('Остаток, шт.', default=0)
    is_popular = models.BooleanField('Популярное', default=False)
    is_active = models.BooleanField('Активен (виден в каталоге)', default=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлён', auto_now=True)

    class Meta:
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)

    @property
    def in_stock(self):
        return self.stock > 0


class ProductImage(models.Model):
    """Дополнительное фото товара (галерея)."""

    product = models.ForeignKey(
        Product, verbose_name='Товар', related_name='gallery',
        on_delete=models.CASCADE,
    )
    image = models.ImageField('Фото', upload_to='products/gallery/')
    order = models.PositiveIntegerField('Порядок сортировки', default=0)

    class Meta:
        verbose_name = 'Фото галереи товара'
        verbose_name_plural = 'Фото галереи товара'
        ordering = ['order']

    def __str__(self):
        return f'{self.product.name} — фото {self.order}'
