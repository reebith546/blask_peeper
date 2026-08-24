from django.db import migrations, models


def fill_in_stock(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    Product.objects.filter(stock__gt=0).update(in_stock=True)
    Product.objects.filter(stock=0).update(in_stock=False)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0002_alter_category_slug_alter_product_slug'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='in_stock',
            field=models.BooleanField(default=True, verbose_name='В наличии'),
        ),
        migrations.RunPython(fill_in_stock, noop),
        migrations.RemoveField(
            model_name='product',
            name='stock',
        ),
    ]
