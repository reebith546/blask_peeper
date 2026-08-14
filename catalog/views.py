from django.shortcuts import get_object_or_404, render

from .models import Category, Product


def product_list(request, category_slug=None):
    categories = Category.objects.filter(is_active=True).order_by('order')
    products = Product.objects.filter(is_active=True).select_related('category')

    current_category = None
    if category_slug:
        current_category = get_object_or_404(Category, slug=category_slug, is_active=True)
        products = products.filter(category=current_category)

    context = {
        'categories': categories,
        'products': products,
        'current_category': current_category,
    }
    return render(request, 'catalog/product_list.html', context)


def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('category').prefetch_related('gallery'),
        slug=slug, is_active=True,
    )
    return render(request, 'catalog/product_detail.html', {'product': product})
