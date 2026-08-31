from django.shortcuts import get_object_or_404, render

from .models import Category, Product
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
    products = Product.objects.filter(is_active=True, in_stock=True).select_related('category')

    current_category = None
    if category_slug:
        current_category = get_object_or_404(Category, slug=category_slug, is_active=True)
        products = products.filter(category=current_category)

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

    context = {
        'categories': categories,
        'products': products,
        'current_category': current_category,
        'current_sort': current_sort,
        'min_price': min_price,
        'max_price': max_price,
    }
    return render(request, 'catalog/product_list.html', context)


def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('category').prefetch_related('gallery'),
        slug=slug, is_active=True, in_stock=True,
    )
    return render(request, 'catalog/product_detail.html', {'product': product})
