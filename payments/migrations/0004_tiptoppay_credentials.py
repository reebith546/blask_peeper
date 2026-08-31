from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0003_paymentsettings'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='paymentsettings',
            name='account',
        ),
        migrations.RenameField(
            model_name='paymentsettings',
            old_name='merchant_key',
            new_name='public_id',
        ),
        migrations.RenameField(
            model_name='paymentsettings',
            old_name='secret',
            new_name='api_secret',
        ),
        migrations.AlterField(
            model_name='paymentsettings',
            name='public_id',
            field=models.CharField(
                blank=True, help_text='Из личного кабинета TipTop Pay, вида «pk_…».',
                max_length=200, verbose_name='Public ID',
            ),
        ),
        migrations.AlterField(
            model_name='paymentsettings',
            name='api_secret',
            field=models.CharField(
                blank=True, help_text='Хранится в базе. Не показывается после сохранения.',
                max_length=255, verbose_name='API Secret (пароль API и ключ подписи)',
            ),
        ),
        migrations.AlterField(
            model_name='paymentsettings',
            name='api_base',
            field=models.URLField(
                blank=True, default='https://api.tiptoppay.kz',
                help_text='Меняйте, только если у вашего аккаунта другой домен API.',
                max_length=200, verbose_name='Адрес API',
            ),
        ),
        migrations.AlterField(
            model_name='payment',
            name='external_id',
            field=models.CharField(
                blank=True, max_length=100, null=True, unique=True,
                verbose_name='ID счёта в шлюзе',
            ),
        ),
        migrations.AlterField(
            model_name='payment',
            name='form_url',
            field=models.URLField(
                blank=True, max_length=500, verbose_name='Ссылка на форму оплаты',
            ),
        ),
    ]
