from django.db.models import Avg, Count, Q
from django.http import Http404, HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from main import seo

from .models import Category, Product, SlugRedirect
from .utils import shuffle


SORT_OPTIONS = {
    'price_asc': 'price',
    'price_desc': '-price',
}


def _parse_price(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def product_list(request, category_slug=None):
    categories = Category.objects.filter(is_active=True).order_by('order')
    # in_stock не фильтруем — «Активен» (показывать на сайте) это отдельная
    # галочка, товара без остатка просто виден с пометкой «Нет в наличии».
    products = Product.objects.filter(is_active=True).select_related('category')

    current_category = None
    if category_slug:
        try:
            current_category = Category.objects.get(slug=category_slug, is_active=True)
        except Category.DoesNotExist:
            # Прежний адрес (кириллический/в другом регистре) — 301 на новый.
            moved = (
                SlugRedirect.objects.filter(old_slug__iexact=category_slug, category__is_active=True)
                .select_related('category').first()
            )
            if moved:
                return HttpResponsePermanentRedirect(moved.category.get_absolute_url())
            raise Http404
        # Товар попадает в категорию и как в основную, и как в дополнительную.
        products = products.filter(
            Q(category=current_category) | Q(extra_categories=current_category)
        ).distinct()

    query = request.GET.get('q', '').strip()
    if query:
        products = products.filter(Q(name__icontains=query) | Q(composition__icontains=query))

    min_price = _parse_price(request.GET.get('min_price'))
    max_price = _parse_price(request.GET.get('max_price'))
    if min_price is not None:
        products = products.filter(price__gte=min_price)
    if max_price is not None:
        products = products.filter(price__lte=max_price)

    current_sort = request.GET.get('sort', '')
    if current_sort in SORT_OPTIONS:
        products = list(products.order_by(SORT_OPTIONS[current_sort]))
    elif current_category:
        # Внутри категории — случайный порядок.
        products = shuffle(products)
    else:
        # В разделе «Все» — сначала популярные сборки (в случайном порядке),
        # затем все остальные товары (тоже в случайном порядке).
        products = list(products)
        popular = shuffle(p for p in products if p.is_popular)
        rest = shuffle(p for p in products if not p.is_popular)
        products = popular + rest

    # Страницы с фильтрами/поиском не должны индексироваться как отдельные:
    # canonical ведёт на чистый URL, а сами они — noindex.
    filtered = bool(query or current_sort or min_price is not None or max_price is not None)
    if current_category:
        seo_title = seo.category_title(current_category)
        seo_description = seo.category_description(current_category)
        crumbs = [
            ('Главная', seo.absolute_url('/')),
            ('Каталог', seo.absolute_url(reverse('catalog:product_list'))),
            (current_category.name, seo.absolute_url(current_category.get_absolute_url())),
        ]
    else:
        seo_title = seo.CATALOG_TITLE
        seo_description = seo.CATALOG_DESCRIPTION
        crumbs = [
            ('Главная', seo.absolute_url('/')),
            ('Каталог', seo.absolute_url(reverse('catalog:product_list'))),
        ]

    context = {
        'seo_title': seo_title,
        'seo_description': seo_description,
        'noindex': filtered,
        'jsonld_extra': [seo.to_jsonld(seo.breadcrumbs_jsonld(crumbs))],
        'categories': categories,
        'products': products,
        'current_category': current_category,
        'current_sort': current_sort,
        'query': query,
        'min_price': min_price,
        'max_price': max_price,
    }
    return render(request, 'catalog/product_list.html', context)


def product_detail(request, slug):
    try:
        product = (
            Product.objects.select_related('category')
            .prefetch_related('gallery', 'extra_categories')
            .get(slug=slug, is_active=True)
        )
    except Product.DoesNotExist:
        moved = (
            SlugRedirect.objects.filter(old_slug__iexact=slug, product__is_active=True)
            .select_related('product').first()
        )
        if moved:
            return HttpResponsePermanentRedirect(moved.product.get_absolute_url())
        raise Http404
    description = seo.product_description(product)

    # Похожие букеты — перелинковка внутри категории (и закрывает «сирот»
    # без внутренних ссылок для поискового робота).
    similar = (
        Product.objects.filter(is_active=True)
        .filter(Q(category=product.category) | Q(extra_categories=product.category))
        .exclude(pk=product.pk)
        .select_related('category')
        .distinct()
        .order_by('-is_popular', '-created_at')[:4]
    )

    from reviews.models import Review

    reviews = Review.objects.filter(product=product, status=Review.Status.PUBLISHED)
    stats = reviews.aggregate(avg=Avg('rating'), count=Count('pk'))
    rating = (
        {'avg': round(stats['avg'], 1), 'count': stats['count']} if stats['count'] else None
    )

    image_urls = [seo.absolute_url(product.image.url)]
    image_urls += [seo.absolute_url(photo.image.url) for photo in product.gallery.all()]

    crumbs = [
        ('Главная', seo.absolute_url('/')),
        ('Каталог', seo.absolute_url(reverse('catalog:product_list'))),
        (product.category.name, seo.absolute_url(product.category.get_absolute_url())),
        (product.name, seo.absolute_url(product.get_absolute_url())),
    ]

    return render(request, 'catalog/product_detail.html', {
        'product': product,
        'similar_products': similar,
        'reviews': reviews[:6],
        'rating': rating,
        'breadcrumbs': crumbs,
        'seo_title': seo.product_title(product),
        'seo_description': description,
        'og_type': 'product',
        'og_image': image_urls[0],
        'jsonld_extra': [
            seo.to_jsonld(seo.product_jsonld(product, image_urls, description, rating)),
            seo.to_jsonld(seo.breadcrumbs_jsonld(crumbs)),
        ],
    })
