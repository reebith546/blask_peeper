from urllib.parse import urlparse

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Category, Product


class SeoSitemap(Sitemap):
    """Адреса строим от боевого домена (SITE_URL), а не от Host запроса —
    чтобы sitemap всегда вёл на основное зеркало (без www, https)."""

    @property
    def protocol(self):
        return urlparse(settings.SITE_URL).scheme or 'https'

    def get_domain(self, site=None):
        return urlparse(settings.SITE_URL).netloc


class StaticSitemap(SeoSitemap):
    changefreq = 'weekly'

    def items(self):
        return ['main:home', 'catalog:product_list', 'main:delivery', 'main:contacts', 'main:about']

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return 1.0 if item == 'main:home' else 0.8


class CategorySitemap(SeoSitemap):
    priority = 0.8
    changefreq = 'weekly'

    def items(self):
        return Category.objects.filter(is_active=True)


class ProductSitemap(SeoSitemap):
    priority = 0.6
    changefreq = 'weekly'

    def items(self):
        return Product.objects.filter(is_active=True)

    def lastmod(self, obj):
        return obj.updated_at
