from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.forms.models import model_to_dict
from django.test import TestCase
from django.urls import reverse

from .admin import TeamAccountChangeForm
from .models import DIRECTOR_GROUP_NAME, SELLER_SECTION_GROUPS, SellerAccount


class SetupRolesCommandTests(TestCase):
    def test_director_group_scope(self):
        call_command('setup_roles')
        group = Group.objects.get(name=DIRECTOR_GROUP_NAME)
        apps = set(group.permissions.values_list('content_type__app_label', flat=True))
        # + audit — но только просмотр журнала
        self.assertEqual(apps, {'catalog', 'orders', 'content', 'reviews', 'accounts', 'audit'})
        codenames = set(group.permissions.values_list('codename', flat=True))
        self.assertFalse(any(c.startswith('delete_') for c in codenames))
        self.assertIn('view_auditevent', codenames)
        self.assertNotIn('add_auditevent', codenames)
        self.assertNotIn('change_auditevent', codenames)

    def test_seller_groups_never_get_audit_access(self):
        call_command('setup_roles')
        for group_name in SELLER_SECTION_GROUPS.values():
            group = Group.objects.get(name=group_name)
            codenames = set(group.permissions.values_list('codename', flat=True))
            self.assertFalse(any('auditevent' in c for c in codenames))

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


class TeamSectionTests(TestCase):
    """Раздел «Сотрудники»: директор ведёт и продавцов, и других директоров,
    но не может выйти за пределы двух ролей и не может создать владельца."""

    def setUp(self):
        call_command('setup_roles')
        self.director_group = Group.objects.get(name=DIRECTOR_GROUP_NAME)
        self.catalog_group = Group.objects.get(name=SELLER_SECTION_GROUPS['catalog'])

        self.owner = User.objects.create_superuser('owner', 'owner@example.com', 'pass12345')

        self.director = User.objects.create_user('director', password='pass12345', is_staff=True)
        self.director.groups.add(self.director_group)

        self.other_director = User.objects.create_user('director2', password='pass12345', is_staff=True)
        self.other_director.groups.add(self.director_group)

        self.seller = User.objects.create_user('seller', password='pass12345', is_staff=True)
        self.seller.groups.add(self.catalog_group)

    # --- видимость списка ------------------------------------------------

    def test_director_sees_sellers_and_other_directors_but_not_owner(self):
        self.client.force_login(self.director)
        response = self.client.get(reverse('admin:accounts_selleraccount_changelist'))
        usernames = {u.username for u in response.context['cl'].queryset}
        self.assertEqual(usernames, {'director', 'director2', 'seller'})

    def test_owner_is_excluded_from_team_section_for_everyone(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('admin:accounts_selleraccount_changelist'))
        usernames = {u.username for u in response.context['cl'].queryset}
        self.assertNotIn('owner', usernames)
        self.assertEqual(usernames, {'director', 'director2', 'seller'})

    # --- форма не даёт расширить доступ --------------------------------

    def test_form_has_no_raw_permission_widgets(self):
        self.client.force_login(self.director)
        response = self.client.get(
            reverse('admin:accounts_selleraccount_change', args=[self.seller.pk])
        )
        fields = response.context['adminform'].form.fields
        self.assertNotIn('groups', fields)
        self.assertNotIn('user_permissions', fields)
        self.assertNotIn('is_superuser', fields)
        self.assertEqual(
            [c[0] for c in fields['role'].choices], ['seller', 'director'],
        )

    # --- директор создаёт директора ----------------------------------

    def test_director_can_create_another_director(self):
        self.client.force_login(self.director)
        response = self.client.post(reverse('admin:accounts_selleraccount_add'), {
            'username': 'newdir',
            'password1': 'Str0ng-Pass-9',
            'password2': 'Str0ng-Pass-9',
            'first_name': 'Новый', 'last_name': 'Директор', 'email': '',
            'role': 'director', 'sections': [],
        })
        self.assertEqual(response.status_code, 302)
        created = User.objects.get(username='newdir')
        self.assertTrue(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertEqual(set(created.groups.values_list('name', flat=True)), {DIRECTOR_GROUP_NAME})

    def test_director_can_promote_seller_to_director(self):
        self.client.force_login(self.director)
        data = self._change_data(self.seller, role='director', sections=[])
        form = TeamAccountChangeForm(data=data, instance=SellerAccount.objects.get(pk=self.seller.pk))
        form._request_user = self.director
        self.assertTrue(form.is_valid(), form.errors)

    def test_seller_role_requires_at_least_one_section(self):
        data = self._change_data(self.seller, role='seller', sections=[])
        form = TeamAccountChangeForm(data=data, instance=SellerAccount.objects.get(pk=self.seller.pk))
        form._request_user = self.director
        self.assertFalse(form.is_valid())
        self.assertIn('sections', form.errors)

    # --- защитные инварианты ------------------------------------------

    def test_cannot_deactivate_own_account(self):
        data = self._change_data(self.director, role='director', sections=[], is_active=False)
        form = TeamAccountChangeForm(data=data, instance=SellerAccount.objects.get(pk=self.director.pk))
        form._request_user = self.director
        self.assertFalse(form.is_valid())
        self.assertIn('is_active', form.errors)

    def test_cannot_orphan_the_last_active_director(self):
        # Оставляем ровно одного активного директора и пробуем его деактивировать.
        self.other_director.is_active = False
        self.other_director.save()

        data = self._change_data(self.director, role='director', sections=[], is_active=False)
        form = TeamAccountChangeForm(data=data, instance=SellerAccount.objects.get(pk=self.director.pk))
        form._request_user = self.other_director  # правит не сам себя
        self.assertFalse(form.is_valid())
        self.assertIn('последний активный директор', ' '.join(form.errors.get('__all__', [])).lower())

    def test_demoting_last_director_to_seller_is_blocked(self):
        self.other_director.delete()
        data = self._change_data(self.director, role='seller', sections=['catalog'])
        form = TeamAccountChangeForm(data=data, instance=SellerAccount.objects.get(pk=self.director.pk))
        form._request_user = self.owner
        self.assertFalse(form.is_valid())

    def test_account_saved_via_section_is_never_superuser(self):
        account = SellerAccount.objects.get(pk=self.seller.pk)
        self.assertFalse(account.is_superuser)
        self.assertTrue(account.is_staff)

    # --- helpers -----------------------------------------------------

    @staticmethod
    def _change_data(user, **overrides):
        data = model_to_dict(user, exclude=['password', 'user_permissions', 'groups', 'last_login'])
        data.setdefault('is_active', user.is_active)
        data.update(overrides)
        # model_to_dict отдаёт None/пусто для необязательных — форме это ок,
        # но role/sections должны присутствовать всегда.
        data.setdefault('role', 'seller')
        data.setdefault('sections', [])
        return {k: ('' if v is None else v) for k, v in data.items()}
