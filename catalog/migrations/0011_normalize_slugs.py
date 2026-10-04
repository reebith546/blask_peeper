from django.db import migrations

from catalog.utils import normalize_slug


def _free(model, slug, pk):
    candidate, n = slug, 2
    while model.objects.filter(slug=candidate).exclude(pk=pk).exists():
        candidate = f'{slug}-{n}'
        n += 1
    return candidate


def normalize_existing_slugs(apps, schema_editor):
    """Кириллические/«с_подчёркиванием»/в верхнем регистре slug-и -> латиница
    через дефис; старый адрес запоминаем для 301-редиректа."""
    Category = apps.get_model('catalog', 'Category')
    Product = apps.get_model('catalog', 'Product')
    SlugRedirect = apps.get_model('catalog', 'SlugRedirect')

    for model, field in ((Category, 'category'), (Product, 'product')):
        for obj in model.objects.all():
            new = normalize_slug(obj.slug or obj.name)
            if not new or new == obj.slug:
                continue
            new = _free(model, new, obj.pk)
            old = obj.slug
            model.objects.filter(pk=obj.pk).update(slug=new)
            if old:
                SlugRedirect.objects.get_or_create(old_slug=old, **{field: obj})


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0010_slug_redirect'),
    ]

    operations = [
        migrations.RunPython(normalize_existing_slugs, migrations.RunPython.noop),
    ]
