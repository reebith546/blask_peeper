from django.db import models
from django.urls import reverse
from .utils import normalize_slug, unique_slug


class Category(models.Model):
    """Категория товаров каталога (например, «Монобукеты», «Свадебные»)."""

    name = models.CharField('Название', max_length=150)
    slug = models.SlugField('Слаг (для URL)', max_length=160, unique=True, blank=True, allow_unicode=True)
    description = models.CharField(
        'Подпись под названием', max_length=200, blank=True,
        help_text='Короткая строка под названием на карточке категории '
                  '(напр. «Букеты для особенных моментов»).',
    )
    image = models.ImageField('Изображение', upload_to='categories/', blank=True, null=True)
    order = models.PositiveIntegerField('Порядок сортировки', default=0)
    is_active = models.BooleanField('Активна', default=True)
    show_on_homepage = models.BooleanField('Показывать на главной', default=True)
    seo_title = models.CharField(
        'SEO-заголовок (title)', max_length=70, blank=True,
        help_text='Необязательно. Если пусто — собирается автоматически из названия.',
    )
    seo_description = models.CharField(
        'SEO-описание (для сниппета в поиске)', max_length=170, blank=True,
        help_text='Необязательно. Если пусто — собирается автоматически.',
    )
    seo_text = models.TextField(
        'SEO-текст под товарами', blank=True,
        help_text='Текст на 1000–2000 знаков внизу страницы категории: о чём эти букеты, '
                  'для кого и для какого повода. Абзацы — через пустую строку.',
    )

    class Meta:
        verbose_name = 'Категория'
        verbose_name_plural = 'Категории'
        ordering = ['order', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        old_slug = type(self).objects.filter(pk=self.pk).values_list('slug', flat=True).first() if self.pk else None
        self.slug = unique_slug(type(self), normalize_slug(self.slug or self.name), self.pk)
        super().save(*args, **kwargs)
        if old_slug and old_slug != self.slug:
            SlugRedirect.remember(old_slug, category=self)

    def get_absolute_url(self):
        return reverse('catalog:product_list_by_category', args=[self.slug])


class Product(models.Model):
    """Товар (букет/композиция)."""

    category = models.ForeignKey(
        Category, verbose_name='Категория', related_name='products',
        on_delete=models.PROTECT,
    )
    extra_categories = models.ManyToManyField(
        Category, verbose_name='Дополнительные категории', related_name='extra_products',
        blank=True,
        help_text='Товар дополнительно покажется и в этих категориях — '
                  'основная категория (поле выше) не дублируется, указывать её здесь не нужно.',
    )
    name = models.CharField('Название', max_length=200)
    subtitle = models.CharField(
        'Подзаголовок (по-русски)', max_length=160, blank=True,
        help_text='Что это за букет простыми словами — например «Букет с кустовыми розами, '
                  'маттиолой и эрингиумом». Выводится под названием и попадает в заголовок '
                  'страницы: по английскому названию букеты не ищут.',
    )
    slug = models.SlugField('Слаг (для URL)', max_length=210, unique=True, blank=True, allow_unicode=True)
    price = models.DecimalField('Цена, ₸', max_digits=10, decimal_places=2)
    discount_price = models.DecimalField(
        'Цена со скидкой, ₸', max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Заполните, чтобы включить скидку — товар начнёт показываться по '
                   'этой цене везде (каталог, карточка, корзина, заказ) с зачёркнутой '
                   'обычной ценой рядом. Оставьте пустым или больше/равно обычной '
                   'цене — скидка не действует.',
    )
    seo_title = models.CharField(
        'SEO-заголовок (title)', max_length=70, blank=True,
        help_text='Необязательно. Если пусто — собирается автоматически.',
    )
    seo_description = models.CharField(
        'SEO-описание (для сниппета в поиске)', max_length=170, blank=True,
        help_text='Необязательно. Если пусто — собирается автоматически из состава и цены.',
    )
    composition = models.TextField(
        'Состав', blank=True,
        help_text='Через запятую в одну строку («Роза 1, Пионы 2, Хризантемы 5») '
                  'или по одному цветку на строке — оба формата работают одинаково.',
    )
    description = models.TextField('Описание', blank=True)
    image = models.ImageField('Главное фото', upload_to='products/')
    in_stock = models.BooleanField('В наличии', default=True)
    is_popular = models.BooleanField('Популярное', default=False)
    popular_order = models.PositiveIntegerField(
        'Позиция в «Популярных сборках»', null=True, blank=True,
        help_text='Порядок в карусели на главной: 1 — первым, 2 — вторым и т.д. '
                  'Товары без номера идут после пронумерованных (сначала новые).',
    )
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
        old_slug = type(self).objects.filter(pk=self.pk).values_list('slug', flat=True).first() if self.pk else None
        self.slug = unique_slug(type(self), normalize_slug(self.slug or self.name), self.pk)
        super().save(*args, **kwargs)
        if old_slug and old_slug != self.slug:
            SlugRedirect.remember(old_slug, product=self)

    def get_absolute_url(self):
        return reverse('catalog:product_detail', args=[self.slug])

    @property
    def image_alt(self):
        """alt для фото: по-русски и с составом — по картинкам цветы ищут активно."""
        from main.seo import composition_names

        detail = self.subtitle or ', '.join(composition_names(self, 3))
        alt = f'Букет «{self.name}»'
        if detail:
            alt += f' — {detail}'
        return f'{alt}. Blackpepper, Алматы'

    @property
    def composition_lines(self):
        """Состав построчно. Можно писать в одну строку через запятую
        («Роза 1, Пионы 2, Хризантемы 5») или по одному цветку на отдельной
        строке (каждая строка — «Роза 1», «Пионы 2» и т.д.) — формат
        определяется автоматически по наличию переноса строки."""
        text = self.composition.strip()
        if not text:
            return []
        if '\n' in text:
            return [line.strip() for line in text.splitlines() if line.strip()]
        return [line.strip() for line in text.split(',') if line.strip()]

    @property
    def all_categories(self):
        """Основная категория + дополнительные — одним списком, без дублей."""
        seen = {self.category_id}
        result = [self.category]
        for extra in self.extra_categories.all():
            if extra.pk not in seen:
                seen.add(extra.pk)
                result.append(extra)
        return result

    @property
    def is_on_sale(self):
        return self.discount_price is not None and self.discount_price < self.price

    @property
    def effective_price(self):
        """Цена, по которой товар реально продаётся — с учётом скидки."""
        return self.discount_price if self.is_on_sale else self.price

    @property
    def discount_percent(self):
        """Скидка в процентах, округлённая до целого — для бейджа «-N%»."""
        if not self.is_on_sale:
            return 0
        return round((1 - self.discount_price / self.price) * 100)


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


class SlugRedirect(models.Model):
    """Прежний адрес категории/товара — чтобы после смены slug старая ссылка
    (из поиска, мессенджеров, закладок) вела 301-редиректом на новую, а вес
    страницы не терялся."""

    old_slug = models.CharField('Прежний slug', max_length=210, db_index=True)
    category = models.ForeignKey(
        Category, verbose_name='Категория', null=True, blank=True,
        related_name='old_slugs', on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        'Product', verbose_name='Товар', null=True, blank=True,
        related_name='old_slugs', on_delete=models.CASCADE,
    )

    class Meta:
        verbose_name = 'Редирект со старого адреса'
        verbose_name_plural = 'Редиректы со старых адресов'

    def __str__(self):
        return f'/{self.old_slug}/ → {self.category or self.product}'

    @classmethod
    def remember(cls, old_slug, category=None, product=None):
        cls.objects.update_or_create(
            old_slug=old_slug, category=category, product=product, defaults={},
        )
