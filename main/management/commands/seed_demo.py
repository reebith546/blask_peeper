import io

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from PIL import Image

from catalog.models import Category, Product
from content.models import HomepageBlock
from delivery.models import DeliveryZone, ShopLocation
from reviews.models import Review

# Заглушки-изображения (однотонные, в тонах бренда) — чтобы можно было
# просмотреть вёрстку в браузере до того, как загружены реальные фото.
PALETTE = ['#BE9554', '#8C6D42', '#D5A866', '#4A3F35', '#C9B79C', '#6B4F3A']


def _placeholder_image(color, size=(900, 1100)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='JPEG', quality=70)
    return ContentFile(buf.getvalue())


class Command(BaseCommand):
    help = 'Наполняет БД демонстрационными данными для проверки вёрстки'

    def handle(self, *args, **options):
        categories_data = [
            'Монобукеты', 'Авторские', 'Свадебные',
            'Композиции', 'Коробки', 'Подарки',
        ]
        categories = []
        for i, name in enumerate(categories_data):
            category, created = Category.objects.get_or_create(
                name=name, defaults={'order': i},
            )
            if created or not category.image:
                category.image.save(
                    f'{category.slug}.jpg',
                    _placeholder_image(PALETTE[i % len(PALETTE)]),
                    save=True,
                )
            categories.append(category)

        products_data = [
            ('Розовый рассвет', 'Пионы, ранункулюсы', 18500, True),
            ('Бархат Бордо', 'Розы, гортензия', 24000, True),
            ('Белый шёлк', 'Тюльпаны, эустома', 16000, True),
            ('Дикий сад', 'Ранункулюсы, зелень', 21500, True),
        ]
        for i, (name, composition, price, is_popular) in enumerate(products_data):
            product, created = Product.objects.get_or_create(
                name=name,
                defaults={
                    'category': categories[i % len(categories)],
                    'composition': composition,
                    'price': price,
                    'is_popular': is_popular,
                    'stock': 10,
                },
            )
            if created or not product.image:
                product.image.save(
                    f'{product.slug}.jpg',
                    _placeholder_image(PALETTE[(i + 2) % len(PALETTE)]),
                    save=True,
                )

        hero, created = HomepageBlock.objects.get_or_create(
            block_type=HomepageBlock.BlockType.HERO,
            defaults={
                'title': 'Цветы с характером.',
                'subtitle': 'Авторская флористика в Алматы — букеты, которые говорят за вас.',
                'button_text': 'Заказать букет',
                'order': 0,
            },
        )
        if created or not hero.image:
            hero.image.save('hero.jpg', _placeholder_image(PALETTE[3], size=(1200, 1400)), save=True)

        for i in range(5):
            block, created = HomepageBlock.objects.get_or_create(
                block_type=HomepageBlock.BlockType.INSTAGRAM,
                order=i,
                defaults={'title': f'Instagram {i + 1}'},
            )
            if created or not block.image:
                block.image.save(
                    f'instagram-{i}.jpg',
                    _placeholder_image(PALETTE[i % len(PALETTE)], size=(700, 700)),
                    save=True,
                )

        reviews_data = [
            ('Айгерим К.', 'Букет выглядел даже лучше, чем на фото. Чувствуется рука настоящего флориста.'),
            ('Дмитрий С.', 'Доставили точно в срок, прислали фото перед отправкой. Очень внимательно к деталям.'),
            ('Мадина Т.', 'Собрала букет через каталог — получилось именно то, что я хотела.'),
            ('Ерлан Б.', 'Заказываю уже третий раз — качество цветов стабильно высокое.'),
        ]
        for author, text in reviews_data:
            Review.objects.get_or_create(
                author_name=author,
                defaults={'text': text, 'rating': 5, 'status': Review.Status.PUBLISHED},
            )

        ShopLocation.objects.get_or_create(
            name='Black Pepper Flower Bar',
            defaults={
                # Ориентировочные координаты центра Алматы — уточните точный адрес магазина в админке.
                'address': 'г. Алматы, ул. Кабанбай батыра, 47',
                'latitude': 43.238949,
                'longitude': 76.889709,
            },
        )

        zones_data = [
            ('Центр', 0, 3, 1000, 30),
            ('Ближние районы', 3, 7, 1500, 45),
            ('Средняя удалённость', 7, 12, 2000, 60),
            ('Дальние районы', 12, 20, 2500, 90),
        ]
        for i, (name, radius_from, radius_to, price, minutes) in enumerate(zones_data):
            DeliveryZone.objects.get_or_create(
                name=name,
                defaults={
                    'radius_from_km': radius_from,
                    'radius_to_km': radius_to,
                    'price': price,
                    'delivery_time_minutes': minutes,
                    'order': i,
                },
            )

        self.stdout.write(self.style.SUCCESS('Демо-данные готовы.'))
