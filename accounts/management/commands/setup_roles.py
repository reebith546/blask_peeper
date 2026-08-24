from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from accounts.models import DIRECTOR_GROUP_NAME, SELLER_SECTION_GROUPS

DIRECTOR_APPS = ['catalog', 'orders', 'content', 'reviews']
ACTIONS = ['view', 'add', 'change']  # без delete — ни у директора, ни у продавцов
OLD_MANAGER_GROUP_NAME = 'Менеджер магазина'  # предыдущая, более грубая версия роли


def _permissions_for_apps(app_labels):
    return Permission.objects.filter(
        content_type__app_label__in=app_labels,
        codename__regex=r'^(%s)_' % '|'.join(ACTIONS),
    )


class Command(BaseCommand):
    help = (
        'Создаёт роль "Директор магазина" и группы-разделы для продавцов '
        '("Продавец: Каталог" и т.д.) с ограниченными правами в Django Admin.'
    )

    def handle(self, *args, **options):
        # Директор: то же, что раньше было у "Менеджера" (товары, заказы,
        # контент, отзывы — без удаления), плюс управление разделом "Продавцы"
        # (но не разделом "Пользователи" целиком — тот виден только владельцу).
        director_group, created = Group.objects.get_or_create(name=DIRECTOR_GROUP_NAME)
        director_permissions = _permissions_for_apps(DIRECTOR_APPS) | _permissions_for_apps(['accounts'])
        director_group.permissions.set(director_permissions)
        verb = 'Создана' if created else 'Обновлена'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} группа "{DIRECTOR_GROUP_NAME}" ({director_permissions.count()} прав).'
        ))

        # Продавцы: по одной группе на раздел — директор сам решает, какие
        # из них выдать конкретному сотруднику при создании учётки.
        for app_label, group_name in SELLER_SECTION_GROUPS.items():
            seller_group, created = Group.objects.get_or_create(name=group_name)
            seller_permissions = _permissions_for_apps([app_label])
            seller_group.permissions.set(seller_permissions)
            verb = 'Создана' if created else 'Обновлена'
            self.stdout.write(self.style.SUCCESS(
                f'{verb} группа "{group_name}" ({seller_permissions.count()} прав).'
            ))

        # Раньше была одна общая группа "Менеджер магазина" — теперь роль
        # разделена на директора и продавцов по разделам. Если кто-то уже был
        # в старой группе, переносим его на все 4 группы продавца (это ровно
        # тот набор прав, что был у "Менеджера"), а саму группу убираем, чтобы
        # она не путалась в списке при создании новых учёток.
        old_group = Group.objects.filter(name=OLD_MANAGER_GROUP_NAME).first()
        if old_group is not None:
            migrated_users = list(old_group.user_set.all())
            all_seller_groups = Group.objects.filter(name__in=SELLER_SECTION_GROUPS.values())
            for user in migrated_users:
                user.groups.add(*all_seller_groups)
                user.groups.remove(old_group)
            old_group.delete()
            if migrated_users:
                usernames = ', '.join(u.username for u in migrated_users)
                self.stdout.write(self.style.WARNING(
                    f'Группа "{OLD_MANAGER_GROUP_NAME}" удалена, права перенесены '
                    f'на группы продавца для: {usernames}.'
                ))
            else:
                self.stdout.write(self.style.WARNING(f'Пустая группа "{OLD_MANAGER_GROUP_NAME}" удалена.'))

        self.stdout.write(self.style.SUCCESS(
            'Готово. Владелец (superuser) назначает директоров через раздел '
            '"Пользователи" (группа "Директор магазина" + "Сотрудник штата"). '
            'Директор заводит продавцов через раздел "Продавцы".'
        ))
