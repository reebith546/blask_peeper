from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

# Менеджер магазина видит и редактирует только эти разделы: товары, категории,
# заказы, блоки главной страницы, отзывы. Настройки доставки, платежей и
# системные разделы (пользователи, группы) ему недоступны — просто не выдаём
# на них права, и Django Admin сам скроет эти разделы из меню.
MANAGER_GROUP_NAME = 'Менеджер магазина'
MANAGER_APPS = ['catalog', 'orders', 'content', 'reviews']
MANAGER_ACTIONS = ['view', 'add', 'change']  # без delete


class Command(BaseCommand):
    help = 'Создаёт группу "Менеджер магазина" с ограниченными правами в Django Admin'

    def handle(self, *args, **options):
        group, created = Group.objects.get_or_create(name=MANAGER_GROUP_NAME)

        permissions = Permission.objects.filter(
            content_type__app_label__in=MANAGER_APPS,
            codename__regex=r'^(%s)_' % '|'.join(MANAGER_ACTIONS),
        )
        group.permissions.set(permissions)

        verb = 'Создана' if created else 'Обновлена'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} группа "{MANAGER_GROUP_NAME}" ({permissions.count()} прав). '
            f'Добавьте сотрудника в эту группу и отметьте ему "is_staff" в Django Admin.'
        ))
