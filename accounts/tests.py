from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import DIRECTOR_GROUP_NAME, SELLER_SECTION_GROUPS, SellerAccount


class SetupRolesCommandTests(TestCase):
    def test_director_group_excludes_delivery_and_payments(self):
        call_command('setup_roles')
        group = Group.objects.get(name=DIRECTOR_GROUP_NAME)
        apps = set(group.permissions.values_list('content_type__app_label', flat=True))
        self.assertEqual(apps, {'catalog', 'orders', 'content', 'reviews', 'accounts'})
        codenames = group.permissions.values_list('codename', flat=True)
        self.assertFalse(any(c.startswith('delete_') for c in codenames))

    def test_seller_groups_are_scoped_to_a_single_app_each(self):
        call_command('setup_roles')
        for app_label, group_name in SELLER_SECTION_GROUPS.items():
            group = Group.objects.get(name=group_name)
            apps = set(group.permissions.values_list('content_type__app_label', flat=True))
            self.assertEqual(apps, {app_label})

    def test_old_manager_group_is_migrated_and_removed(self):
        old_group = Group.objects.create(name='Менеджер магазина')
        legacy_user = User.objects.create_user('legacy_manager', is_staff=True)
        legacy_user.groups.add(old_group)

        call_command('setup_roles')

        self.assertFalse(Group.objects.filter(name='Менеджер магазина').exists())
        legacy_user.refresh_from_db()
        seller_group_names = set(legacy_user.groups.values_list('name', flat=True))
        self.assertEqual(seller_group_names, set(SELLER_SECTION_GROUPS.values()))


class DirectorAdminRestrictionTests(TestCase):
    """Директор не должен видеть/трогать владельца, других директоров,
    и не должен уметь выдать продавцу (или себе) роль директора."""

    def setUp(self):
        call_command('setup_roles')
        self.director_group = Group.objects.get(name=DIRECTOR_GROUP_NAME)

        self.owner = User.objects.create_superuser('owner', 'owner@example.com', 'pass12345')

        self.director = User.objects.create_user('director', password='pass12345', is_staff=True)
        self.director.groups.add(self.director_group)

        self.other_director = User.objects.create_user('director2', password='pass12345', is_staff=True)
        self.other_director.groups.add(self.director_group)

        self.seller = User.objects.create_user('seller', password='pass12345', is_staff=True)
        self.seller.groups.add(Group.objects.get(name=SELLER_SECTION_GROUPS['catalog']))

    def test_director_sees_only_sellers_in_changelist(self):
        self.client.force_login(self.director)
        response = self.client.get(reverse('admin:accounts_selleraccount_changelist'))
        self.assertEqual(response.status_code, 200)
        usernames = {u.username for u in response.context['cl'].queryset}
        self.assertEqual(usernames, {'seller'})

    def test_director_cannot_assign_director_group_to_seller(self):
        self.client.force_login(self.director)
        response = self.client.get(
            reverse('admin:accounts_selleraccount_change', args=[self.seller.pk])
        )
        self.assertEqual(response.status_code, 200)
        available_groups = set(response.context['adminform'].form.fields['groups'].queryset)
        self.assertNotIn(self.director_group, available_groups)

    def test_owner_sees_everyone_including_directors(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('admin:accounts_selleraccount_changelist'))
        usernames = {u.username for u in response.context['cl'].queryset}
        self.assertEqual(usernames, {'owner', 'director', 'director2', 'seller'})

    def test_seller_account_created_via_admin_is_never_superuser(self):
        seller_account = SellerAccount.objects.get(pk=self.seller.pk)
        self.assertFalse(seller_account.is_superuser)
        self.assertTrue(seller_account.is_staff)
