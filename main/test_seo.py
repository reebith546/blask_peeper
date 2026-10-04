import json
import re
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Category, Product
from delivery.models import DeliveryZone, ShopLocation
from main import seo
from main.tests import _make_test_image
from main.templatetags.shop_extras import thumb
from reviews.models import Review

SITE = 'https://blackpepperflowerbar.kz'


def _ld_blocks(html):
    return [
        json.loads(raw) for raw in
        re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, flags=re.S)
    ]


class SeoFixtureMixin:
    def setUp(self):
        self.category = Category.objects.create(name='Букеты роз', slug='bukety-roz')
        self.product = Product.objects.create(
            name='Black Amour', category=self.category, price=Decimal('29500'),
            composition='Роза - 7 шт\nМаттиола - 3 шт.\nЭрингиум 2', in_stock=True,
            is_active=True, image=_make_test_image(),
        )


class RobotsAndSitemapTests(SeoFixtureMixin, TestCase):
    def test_robots_txt_blocks_service_pages_and_points_to_sitemap(self):
        resp = self.client.get('/robots.txt')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/plain; charset=utf-8')
        body = resp.content.decode()
        self.assertIn('Disallow: /admin/', body)
        # Страницы с noindex/canonical нельзя закрывать в robots.txt: робот
        # не увидит мета-тег.
        for path in ('/cart/', '/checkout/', '/order/', '/payments/', '?sort=', '?q='):
            self.assertNotIn(f'Disallow: {path}', body)
            self.assertNotIn(f'Disallow: /*{path}', body)
        self.assertIn('Clean-param:', body)
        self.assertIn(f'Sitemap: {SITE}/sitemap.xml', body)

    def test_sitemap_lists_pages_categories_and_products_on_the_main_domain(self):
        resp = self.client.get('/sitemap.xml')
        self.assertEqual(resp.status_code, 200)
        xml = resp.content.decode()
        self.assertIn(f'<loc>{SITE}/</loc>', xml)
        self.assertIn(f'<loc>{SITE}/catalog/bukety-roz/</loc>', xml)
        self.assertIn(f'<loc>{SITE}/catalog/product/black-amour/</loc>', xml)
        self.assertIn(f'<loc>{SITE}/delivery/</loc>', xml)
        self.assertNotIn('/cart/', xml)

    def test_sitemap_skips_inactive_products(self):
        self.product.is_active = False
        self.product.save(update_fields=['is_active'])
        self.assertNotIn('black-amour', self.client.get('/sitemap.xml').content.decode())


class HomeSeoTests(SeoFixtureMixin, TestCase):
    def test_home_has_single_h1_with_keyword_and_meta_tags(self):
        html = self.client.get('/').content.decode()
        self.assertEqual(len(re.findall(r'<h1[ >]', html)), 1)
        self.assertIn('Доставка цветов и авторских букетов в Алматы', html)
        self.assertIn(f'<title>{seo.HOME_TITLE}</title>', html)
        self.assertIn('<meta name="description"', html)
        self.assertIn(f'<link rel="canonical" href="{SITE}/">', html)
        self.assertIn('<meta property="og:title"', html)
        self.assertIn('og-default.jpg', html)

    def test_home_has_florist_and_faq_json_ld(self):
        types = [block['@type'] for block in _ld_blocks(self.client.get('/').content.decode())]
        self.assertIn('Florist', types)
        self.assertIn('FAQPage', types)

    def test_florist_json_ld_includes_shop_geo_when_known(self):
        ShopLocation.objects.create(latitude=Decimal('43.250000'), longitude=Decimal('76.900000'))
        florist = next(
            b for b in _ld_blocks(self.client.get('/').content.decode()) if b['@type'] == 'Florist'
        )
        self.assertEqual(florist['geo']['latitude'], 43.25)
        self.assertEqual(florist['address']['addressLocality'], 'Алматы')

    @override_settings(YANDEX_METRIKA_ID='12345678', GA4_MEASUREMENT_ID='G-TEST123')
    def test_analytics_snippets_appear_only_when_ids_are_set(self):
        html = self.client.get('/').content.decode()
        self.assertIn('mc.yandex.ru/metrika/tag.js', html)
        self.assertIn('G-TEST123', html)

    @override_settings(GTM_ID='')
    def test_no_analytics_when_ids_are_empty(self):
        html = self.client.get('/').content.decode()
        self.assertNotIn('mc.yandex.ru', html)
        self.assertNotIn('googletagmanager', html)

    def test_google_tag_manager_is_installed_by_default(self):
        html = self.client.get('/').content.decode()
        self.assertEqual(html.count("'dataLayer','GTM-PN4X74NN'"), 1)
        self.assertIn('googletagmanager.com/ns.html?id=GTM-PN4X74NN', html)
        # скрипт — в <head>, noscript-iframe — сразу после <body>
        self.assertLess(html.index("'dataLayer','GTM-PN4X74NN'"), html.index('</head>'))
        self.assertLess(html.index('<body>'), html.index('ns.html?id=GTM-PN4X74NN'))

    def test_gtm_is_not_added_to_admin(self):
        self.assertNotIn('googletagmanager', self.client.get('/admin/login/').content.decode())


class CatalogSeoTests(SeoFixtureMixin, TestCase):
    def test_catalog_title_and_canonical(self):
        html = self.client.get(reverse('catalog:product_list')).content.decode()
        self.assertIn(f'<title>{seo.CATALOG_TITLE}</title>', html)
        self.assertIn(f'<link rel="canonical" href="{SITE}/catalog/">', html)
        self.assertNotIn('noindex', html)

    def test_filtered_catalog_is_noindex_with_clean_canonical(self):
        for params in ({'q': 'роза'}, {'sort': 'price_asc'}, {'min_price': '1000'}):
            with self.subTest(params=params):
                html = self.client.get(reverse('catalog:product_list'), params).content.decode()
                self.assertIn('<meta name="robots" content="noindex, follow">', html)
                self.assertIn(f'<link rel="canonical" href="{SITE}/catalog/">', html)

    def test_category_page_uses_generated_or_custom_title(self):
        url = reverse('catalog:product_list_by_category', args=[self.category.slug])
        self.assertIn(
            'Букеты роз в Алматы — купить с доставкой', self.client.get(url).content.decode(),
        )
        self.category.seo_title = 'Розы с доставкой в Алматы'
        self.category.seo_description = 'Свой сниппет категории'
        self.category.save()
        html = self.client.get(url).content.decode()
        self.assertIn('<title>Розы с доставкой в Алматы</title>', html)
        self.assertIn('content="Свой сниппет категории"', html)

    def test_category_intro_and_seo_text_are_shown(self):
        self.category.description = 'Классика и авторские сборки'
        self.category.seo_text = 'Первый абзац.\n\nВторой абзац.'
        self.category.save()
        html = self.client.get(
            reverse('catalog:product_list_by_category', args=[self.category.slug])
        ).content.decode()
        self.assertIn('Классика и авторские сборки', html)
        self.assertIn('<p>Второй абзац.</p>', html)


class ProductSeoTests(SeoFixtureMixin, TestCase):
    def _page(self):
        return self.client.get(self.product.get_absolute_url()).content.decode()

    def test_product_title_has_keywords_but_no_price(self):
        title = seo.product_title(self.product)
        self.assertEqual(title, 'Букет «Black Amour» в Алматы: роза, маттиола · Blackpepper Flower Bar')
        self.assertNotIn('₸', title)
        self.assertIn('29 500 ₸', seo.product_description(self.product))
        self.assertIn(f'<title>{title}</title>', self._page())

    def test_composition_names_strip_quantities(self):
        self.assertEqual(
            seo.composition_names(self.product), ['роза', 'маттиола', 'эрингиум'],
        )

    def test_custom_seo_fields_override_generated_ones(self):
        self.product.seo_title = 'Мой заголовок'
        self.product.seo_description = 'Моё описание'
        self.product.save()
        html = self._page()
        self.assertIn('<title>Мой заголовок</title>', html)
        self.assertIn('content="Моё описание"', html)

    def test_product_json_ld_has_offer_and_breadcrumbs(self):
        blocks = {b['@type']: b for b in _ld_blocks(self._page())}
        offer = blocks['Product']['offers']
        self.assertEqual(offer['price'], '29500')
        self.assertEqual(offer['priceCurrency'], 'KZT')
        self.assertTrue(offer['availability'].endswith('InStock'))
        self.assertNotIn('aggregateRating', blocks['Product'])
        crumbs = blocks['BreadcrumbList']['itemListElement']
        self.assertEqual([c['name'] for c in crumbs][-1], 'Black Amour')

    def test_out_of_stock_product_marked_in_json_ld(self):
        self.product.in_stock = False
        self.product.save()
        product_ld = next(b for b in _ld_blocks(self._page()) if b['@type'] == 'Product')
        self.assertTrue(product_ld['offers']['availability'].endswith('OutOfStock'))

    def test_aggregate_rating_from_published_reviews_only(self):
        Review.objects.create(product=self.product, author_name='А', text='ок', rating=5,
                              status=Review.Status.PUBLISHED)
        Review.objects.create(product=self.product, author_name='Б', text='ок', rating=4,
                              status=Review.Status.PUBLISHED)
        Review.objects.create(product=self.product, author_name='В', text='скрыт', rating=1)
        product_ld = next(b for b in _ld_blocks(self._page()) if b['@type'] == 'Product')
        self.assertEqual(product_ld['aggregateRating']['reviewCount'], '2')
        self.assertEqual(product_ld['aggregateRating']['ratingValue'], '4.5')

    def test_open_graph_image_is_the_bouquet_photo(self):
        html = self._page()
        self.assertIn('<meta property="og:type" content="product">', html)
        og_image = re.search(r'property="og:image" content="([^"]+)"', html).group(1)
        self.assertTrue(og_image.startswith(SITE + '/media/'))

    def test_page_has_visible_breadcrumbs_and_similar_bouquets(self):
        other = Product.objects.create(
            name='Rose Garden', category=self.category, price=Decimal('15000'),
            is_active=True, image=_make_test_image(),
        )
        html = self._page()
        self.assertIn('class="breadcrumbs', html)
        self.assertIn('Похожие букеты', html)
        self.assertIn(other.name, html)

    def test_page_without_description_still_has_unique_text(self):
        html = self._page()
        self.assertIn('Авторский букет «Black Amour» собирает флорист', html)

    def test_images_are_lazy_and_have_russian_alt(self):
        html = self.client.get(reverse('catalog:product_list')).content.decode()
        self.assertIn('loading="lazy"', html)
        self.assertIn('alt="Букет «Black Amour»', html)
        self.assertIn('/thumbs/600x750/', html)

    def test_subtitle_is_shown_and_used_in_alt_and_description(self):
        self.product.subtitle = 'Букет с кустовыми розами и маттиолой'
        self.product.save()
        html = self._page()
        self.assertIn('product-detail__subtitle', html)
        self.assertIn('Букет с кустовыми розами и маттиолой', self.product.image_alt)
        self.assertIn('букет с кустовыми розами', seo.product_description(self.product))


class ServicePagesTests(SeoFixtureMixin, TestCase):
    def test_cart_checkout_and_order_pages_are_noindex(self):
        html = self.client.get(reverse('main:cart')).content.decode()
        self.assertIn('<meta name="robots" content="noindex, nofollow">', html)

    def test_custom_404_page(self):
        resp = self.client.get('/no-such-page/')
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, 'Такой страницы нет', status_code=404)
        self.assertContains(resp, 'noindex', status_code=404)

    def test_delivery_page_lists_active_zones(self):
        DeliveryZone.objects.create(
            name='Центр', radius_from_km=0, radius_to_km=5, price=Decimal('1500'),
        )
        DeliveryZone.objects.create(
            name='Скрытая', radius_from_km=5, radius_to_km=9, price=Decimal('2500'), is_active=False,
        )
        html = self.client.get(reverse('main:delivery')).content.decode()
        self.assertIn('Центр', html)
        self.assertIn('1 500', html)
        self.assertNotIn('Скрытая', html)
        self.assertIn(f'<title>{seo.DELIVERY_TITLE}</title>', html)

    def test_contacts_page_has_phone_link_and_map_when_location_known(self):
        ShopLocation.objects.create(latitude=Decimal('43.25'), longitude=Decimal('76.90'))
        html = self.client.get(reverse('main:contacts')).content.decode()
        self.assertIn('href="tel:+77066644144"', html)
        self.assertIn('yandex.ru/map-widget', html)

    def test_foreign_email_is_gone_from_about_page(self):
        self.assertNotIn('characterflowers', self.client.get(reverse('main:about')).content.decode())


class ThumbFilterTests(SeoFixtureMixin, TestCase):
    def test_thumb_creates_resized_webp_once(self):
        url = thumb(self.product.image, '120x150')
        self.assertIn('/thumbs/120x150/', url)
        self.assertTrue(url.endswith('.webp'))
        self.assertEqual(thumb(self.product.image, '120x150'), url)

    def test_thumb_width_only_keeps_aspect_ratio(self):
        self.assertIn('/thumbs/200/', thumb(self.product.image, '200'))

    def test_thumb_falls_back_to_original_when_file_missing(self):
        self.product.image.storage.delete(self.product.image.name)
        self.assertEqual(thumb(self.product.image, '120x150'), self.product.image.url)

    def test_thumb_of_empty_image_is_empty(self):
        self.assertEqual(thumb(None, '100x100'), '')


class SlugRedirectTests(SeoFixtureMixin, TestCase):
    def test_normalize_slug_transliterates_cyrillic(self):
        from catalog.utils import normalize_slug
        self.assertEqual(normalize_slug('Дикий сад'), 'dikiy-sad')
        self.assertEqual(normalize_slug('Wild_Kilimanjaro 87'), 'wild-kilimanjaro-87')

    def test_slug_is_latin_and_unique(self):
        a = Product.objects.create(name='Дикий сад', category=self.category, price=1000)
        b = Product.objects.create(name='Дикий сад', category=self.category, price=1000)
        self.assertEqual(a.slug, 'dikiy-sad')
        self.assertEqual(b.slug, 'dikiy-sad-2')

    def test_old_product_slug_redirects_301(self):
        old = self.product.slug
        self.product.slug = 'black-amour-new'
        self.product.save()
        response = self.client.get(reverse('catalog:product_detail', args=[old]))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.product.get_absolute_url())

    def test_old_category_slug_redirects_301(self):
        old = self.category.slug
        self.category.slug = 'rozy'
        self.category.save()
        response = self.client.get(reverse('catalog:product_list_by_category', args=[old]))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.category.get_absolute_url())


class HiddenProductAndSessionTests(SeoFixtureMixin, TestCase):
    def test_deactivated_product_redirects_to_its_category(self):
        url = self.product.get_absolute_url()
        self.product.is_active = False
        self.product.save(update_fields=['is_active'])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.category.get_absolute_url())

    def test_unknown_product_is_still_404(self):
        self.assertEqual(self.client.get('/catalog/product/net-takogo/').status_code, 404)

    def test_anonymous_page_view_does_not_create_a_session(self):
        for url in ('/', '/catalog/', self.product.get_absolute_url()):
            response = self.client.get(url)
            self.assertNotIn('sessionid', response.cookies, url)

    def test_adding_to_cart_still_creates_session_and_keeps_item(self):
        self.client.post(reverse('main:cart_add', args=[self.product.pk]))
        self.assertIn('sessionid', self.client.cookies)
        self.assertContains(self.client.get(reverse('main:cart')), 'Black Amour')

    def test_catalog_cards_share_a_single_csrf_form(self):
        html = self.client.get('/catalog/').content.decode()
        self.assertEqual(html.count('name="csrfmiddlewaretoken"'), 1)
        self.assertIn('form="card-cart-form"', html)
        self.assertIn(f'formaction="{reverse("main:cart_add", args=[self.product.pk])}"', html)
