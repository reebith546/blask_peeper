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
        if not block:
            return {'site_hero_image': ''}
        from .templatetags.shop_extras import thumb

        return {'site_hero_image': thumb(block.image, '1600')}
    except Exception:
        return {'site_hero_image': ''}


def seo(request):
    """Общие SEO-данные для base.html: canonical, JSON-LD магазина, счётчики."""
    from django.conf import settings

    from . import seo as seo_helpers

    if request.path.startswith('/admin/'):
        return {}
    location = None
    try:
        from delivery.models import ShopLocation

        location = ShopLocation.objects.first()
    except Exception:
        pass
    return {
        'site_url': settings.SITE_URL.rstrip('/'),
        'canonical_url': seo_helpers.absolute_url(request.path),
        'og_default_image': seo_helpers.absolute_url('/static/images/og-default.jpg'),
        'org_jsonld': seo_helpers.to_jsonld(seo_helpers.organization_jsonld(location)),
        'shop': {
            'name': settings.SHOP_NAME,
            'phone': settings.SHOP_PHONE,
            'phone_display': settings.SHOP_PHONE_DISPLAY,
            'address': settings.SHOP_ADDRESS_FULL,
            'email': settings.SHOP_EMAIL,
        },
        'metrika_id': settings.YANDEX_METRIKA_ID,
        'ga4_id': settings.GA4_MEASUREMENT_ID,
    }
