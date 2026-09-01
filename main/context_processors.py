from .cart import Cart


def cart(request):
    return {'cart': Cart(request)}


def hero_image(request):
    """URL фонового фото hero-баннера — одно на весь сайт.

    Шапки внутренних страниц (каталог, «О нас», оферта, политика) используют
    то же изображение, что и hero на главной: берём активный блок «Hero-баннер»
    с наименьшим порядком. Любая ошибка (нет блока / нет картинки / БД ещё не
    готова) — просто пустая строка, шапка останется просто тёмной.
    """
    try:
        from content.models import HomepageBlock

        block = (
            HomepageBlock.objects
            .filter(block_type=HomepageBlock.BlockType.HERO, is_active=True)
            .exclude(image='')
            .order_by('order')
            .first()
        )
        return {'site_hero_image': block.image.url if block else ''}
    except Exception:
        return {'site_hero_image': ''}
