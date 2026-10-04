"""SEO-хелперы: заголовки, описания и JSON-LD (Schema.org) для страниц сайта.

Заголовки/описания собираются автоматически, а менеджер может переопределить их
в админке (поля seo_title / seo_description у товара и категории).
"""
import json
import re

from django.conf import settings
from django.utils.encoding import iri_to_uri
from django.utils.text import Truncator

BRAND = 'Blackpepper'

HOME_TITLE = f'Доставка цветов в Алматы — авторские букеты · {BRAND}'
HOME_DESCRIPTION = (
    'Авторские букеты с доставкой по Алматы от 40 минут. Свежие цветы, '
    'фото готового букета перед отправкой. Заказ онлайн или по телефону '
    f'{settings.SHOP_PHONE_DISPLAY}.'
)
CATALOG_TITLE = f'Купить букет в Алматы с доставкой — каталог цветов · {BRAND}'
CATALOG_DESCRIPTION = (
    'Каталог авторских букетов Blackpepper Flower Bar: розы, пионы, тюльпаны, '
    'композиции в коробках. Доставка по Алматы от 40 минут, оплата онлайн.'
)
ABOUT_TITLE = f'Цветочный бар {BRAND} на Желтоксан, 87а — Алматы'
ABOUT_DESCRIPTION = (
    'Blackpepper Flower Bar — авторская флористика в Алматы: свежие цветы, '
    'букеты ручной работы и доставка по городу. Адрес, телефон, часы работы.'
)
DELIVERY_TITLE = f'Доставка цветов по Алматы — стоимость и сроки · {BRAND}'
DELIVERY_DESCRIPTION = (
    'Доставка цветов по Алматы от 40 минут: стоимость по районам, самовывоз с '
    'ул. Желтоксан, 87а, оплата онлайн. Фото готового букета перед отправкой.'
)
CONTACTS_TITLE = f'Контакты — цветочный магазин на Желтоксан, 87а, Алматы · {BRAND}'
CONTACTS_DESCRIPTION = (
    f'Blackpepper Flower Bar: {settings.SHOP_ADDRESS_FULL}, телефон '
    f'{settings.SHOP_PHONE_DISPLAY}, ежедневно 11:00–21:00. Карта проезда.'
)
LEGAL_OFFER_TITLE = f'Публичная оферта — {BRAND}'
LEGAL_PRIVACY_TITLE = f'Политика конфиденциальности — {BRAND}'


def absolute_url(path):
    """Абсолютный URL с боевым доменом (SITE_URL) — для canonical, OG, sitemap."""
    return settings.SITE_URL.rstrip('/') + iri_to_uri(path)


def to_jsonld(data):
    """JSON для <script type="application/ld+json">: безопасно для вставки в HTML."""
    raw = json.dumps(data, ensure_ascii=False)
    return raw.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def money(value):
    """18500 -> «18 500»."""
    return f'{int(round(float(value))):,}'.replace(',', ' ')


_QTY_TAIL = re.compile(r'\s*[-–—:,]?\s*\d+.*$')


def composition_names(product, limit=None):
    """Названия цветов из состава без количеств: «Роза — 7 шт.» -> «роза»."""
    names = []
    for line in product.composition_lines:
        name = _QTY_TAIL.sub('', line).strip().lower()
        if name and name not in names:
            names.append(name)
    return names[:limit] if limit else names


# --- товар -------------------------------------------------------------

def product_title(product):
    if product.seo_title:
        return product.seo_title
    price = money(product.effective_price)
    base = f'Букет «{product.name}»'
    names = composition_names(product, 2)
    # Состав в именительном падеже, поэтому не «с розой…», а «: роза, маттиола».
    with_flowers = f'{base}: {", ".join(names)}' if names else base
    for head in (with_flowers, base):
        title = f'{head} — {price} ₸, доставка по Алматы'
        if len(title) <= 78:
            return title
    return title


def product_description(product):
    if product.seo_description:
        return product.seo_description
    parts = [f'Букет «{product.name}»']
    if product.subtitle:
        parts[0] += f' — {product.subtitle[0].lower()}{product.subtitle[1:]}'
    names = composition_names(product, 5)
    text = parts[0] + '.'
    if names:
        text += f' Состав: {", ".join(names)}.'
    text += f' Цена {money(product.effective_price)} ₸.'
    text += ' Доставка по Алматы от 40 минут, фото перед отправкой.'
    return Truncator(text).chars(240)


# --- категория ---------------------------------------------------------

def category_title(category):
    return category.seo_title or f'{category.name} в Алматы — купить с доставкой · {BRAND}'


def category_description(category):
    if category.seo_description:
        return category.seo_description
    intro = f'{category.description.rstrip(".")}. ' if category.description else ''
    return Truncator(
        f'{category.name}: {intro}Авторские букеты с доставкой по Алматы от 40 минут, '
        'фото готового букета перед отправкой.'
    ).chars(240)


# --- JSON-LD -----------------------------------------------------------

def organization_jsonld(location=None):
    data = {
        '@context': 'https://schema.org',
        '@type': 'Florist',
        'name': settings.SHOP_NAME,
        'url': settings.SITE_URL.rstrip('/') + '/',
        'logo': absolute_url('/static/images/logo-black.png'),
        'image': absolute_url('/static/images/og-default.jpg'),
        'telephone': settings.SHOP_PHONE,
        'priceRange': '₸₸',
        'address': {
            '@type': 'PostalAddress',
            'streetAddress': settings.SHOP_STREET,
            'addressLocality': settings.SHOP_CITY,
            'addressCountry': 'KZ',
        },
        'openingHoursSpecification': [{
            '@type': 'OpeningHoursSpecification',
            'dayOfWeek': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
            'opens': '11:00',
            'closes': '21:00',
        }],
        'areaServed': ['Алматы', 'Алматинская область'],
        'sameAs': ['https://www.instagram.com/blackpepperflowerbar'],
    }
    if settings.SHOP_EMAIL:
        data['email'] = settings.SHOP_EMAIL
    if location is not None:
        data['geo'] = {
            '@type': 'GeoCoordinates',
            'latitude': float(location.latitude),
            'longitude': float(location.longitude),
        }
    return data


def breadcrumbs_jsonld(items):
    """items — [(название, абсолютный_url), ...] от главной к текущей странице."""
    return {
        '@context': 'https://schema.org',
        '@type': 'BreadcrumbList',
        'itemListElement': [
            {'@type': 'ListItem', 'position': i, 'name': name, 'item': url}
            for i, (name, url) in enumerate(items, start=1)
        ],
    }


def product_jsonld(product, image_urls, description, rating=None):
    data = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'name': product.name,
        'image': image_urls,
        'description': description,
        'brand': {'@type': 'Brand', 'name': settings.SHOP_NAME},
        'offers': {
            '@type': 'Offer',
            'url': absolute_url(product.get_absolute_url()),
            'priceCurrency': 'KZT',
            'price': str(int(round(float(product.effective_price)))),
            'availability': 'https://schema.org/' + ('InStock' if product.in_stock else 'OutOfStock'),
        },
    }
    if rating and rating['count']:
        data['aggregateRating'] = {
            '@type': 'AggregateRating',
            'ratingValue': str(rating['avg']),
            'reviewCount': str(rating['count']),
        }
    return data


def faq_jsonld(pairs):
    return {
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        'mainEntity': [
            {'@type': 'Question', 'name': q,
             'acceptedAnswer': {'@type': 'Answer', 'text': a}}
            for q, a in pairs
        ],
    }
