from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import DIRECTOR_GROUP_NAME, SELLER_SECTION_GROUPS
from catalog.models import Category

from .admin import AuditEventAdmin
from .models import AuditEvent
from .services import record, role_of


class AuditEventModelTests(TestCase):
    def test_entry_is_append_only(self):
        event = AuditEvent.objects.create(action=AuditEvent.Action.LOGIN, actor_role=AuditEvent.Role.OWNER)
        event.outcome = AuditEvent.Outcome.DENIED
        with self.assertRaises(ValueError):
            event.save()

    def test_only_view_permission_exists(self):
        from django.contrib.auth.models import Permission

        codenames = set(
            Permission.objects.filter(content_type__app_label='audit').values_list('codename', flat=True)
        )
        self.assertEqual(codenames, {'view_auditevent'})

    def test_cleanup_command_removes_old_entries(self):
        old = AuditEvent.objects.create(action=AuditEvent.Action.VIEW, actor_role=AuditEvent.Role.SELLER)
        AuditEvent.objects.filter(pk=old.pk).update(
            timestamp=old.timestamp.replace(year=old.timestamp.year - 3),
        )
        AuditEvent.objects.create(action=AuditEvent.Action.VIEW, actor_role=AuditEvent.Role.SELLER)

        call_command('cleanup_audit')

        self.assertFalse(AuditEvent.objects.filter(pk=old.pk).exists())
        # свежая запись осталась + системная запись о самой чистке
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.SYSTEM).exists())


class RoleResolutionTests(TestCase):
    def setUp(self):
        call_command('setup_roles')

    def test_role_of(self):
        owner = User.objects.create_superuser('o', 'o@e.com', 'x')
        director = User.objects.create_user('d', is_staff=True)
        director.groups.add(Group.objects.get(name=DIRECTOR_GROUP_NAME))
        seller = User.objects.create_user('s', is_staff=True)
        seller.groups.add(Group.objects.get(name=SELLER_SECTION_GROUPS['catalog']))

        self.assertEqual(role_of(owner), AuditEvent.Role.OWNER)
        self.assertEqual(role_of(director), AuditEvent.Role.DIRECTOR)
        self.assertEqual(role_of(seller), AuditEvent.Role.SELLER)
        self.assertEqual(role_of(None), AuditEvent.Role.ANON)


class AuditVisibilityTests(TestCase):
    def setUp(self):
        call_command('setup_roles')
        self.factory = RequestFactory()
        self.admin = AuditEventAdmin(AuditEvent, admin_site=None)

        self.owner = User.objects.create_superuser('owner', 'owner@e.com', 'x')
        self.director = User.objects.create_user('director', is_staff=True)
        self.director.groups.add(Group.objects.get(name=DIRECTOR_GROUP_NAME))
        self.seller = User.objects.create_user('seller', is_staff=True)
        self.seller.groups.add(Group.objects.get(name=SELLER_SECTION_GROUPS['catalog']))

        AuditEvent.objects.create(action=AuditEvent.Action.LOGIN, actor=self.owner, actor_role=AuditEvent.Role.OWNER)
        AuditEvent.objects.create(action=AuditEvent.Action.UPDATE, actor=self.director, actor_role=AuditEvent.Role.DIRECTOR)
        AuditEvent.objects.create(action=AuditEvent.Action.UPDATE, actor=self.seller, actor_role=AuditEvent.Role.SELLER)
        AuditEvent.objects.create(action=AuditEvent.Action.SYSTEM, actor_role=AuditEvent.Role.SYSTEM)

    def _request(self, user):
        request = self.factory.get('/admin/audit/auditevent/')
        request.user = user
        return request

    def test_seller_has_no_access(self):
        self.assertFalse(self.admin.has_view_permission(self._request(self.seller)))
        self.assertFalse(self.admin.has_module_permission(self._request(self.seller)))

    def test_director_sees_staff_events_but_not_owner_or_system(self):
        request = self._request(self.director)
        self.assertTrue(self.admin.has_view_permission(request))
        roles = set(self.admin.get_queryset(request).values_list('actor_role', flat=True))
        self.assertEqual(roles, {AuditEvent.Role.DIRECTOR, AuditEvent.Role.SELLER})

    def test_owner_sees_everything(self):
        request = self._request(self.owner)
        roles = set(self.admin.get_queryset(request).values_list('actor_role', flat=True))
        self.assertEqual(
            roles,
            {AuditEvent.Role.OWNER, AuditEvent.Role.DIRECTOR, AuditEvent.Role.SELLER, AuditEvent.Role.SYSTEM},
        )

    def test_admin_is_read_only(self):
        request = self._request(self.owner)
        self.assertFalse(self.admin.has_add_permission(request))
        self.assertFalse(self.admin.has_change_permission(request))
        self.assertFalse(self.admin.has_delete_permission(request))


class AuditCaptureTests(TestCase):
    def setUp(self):
        call_command('setup_roles')
        self.owner = User.objects.create_superuser('owner', 'owner@e.com', 'pass12345')

    def test_admin_create_is_logged(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('admin:catalog_category_add'), {
            'name': 'Пионы', 'slug': 'piony', 'order': 0,
            'products-TOTAL_FORMS': '0', 'products-INITIAL_FORMS': '0',
        })
        category = Category.objects.get(slug='piony')
        event = AuditEvent.objects.filter(action=AuditEvent.Action.CREATE, target_object_id=str(category.pk)).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.actor_role, AuditEvent.Role.OWNER)

    def test_admin_change_records_field_diff(self):
        category = Category.objects.create(name='Розы', slug='rozy')
        self.client.force_login(self.owner)
        self.client.post(reverse('admin:catalog_category_change', args=[category.pk]), {
            'name': 'Розы премиум', 'slug': 'rozy', 'order': 0,
            'products-TOTAL_FORMS': '0', 'products-INITIAL_FORMS': '0',
        })
        event = AuditEvent.objects.filter(action=AuditEvent.Action.UPDATE, target_object_id=str(category.pk)).first()
        self.assertIsNotNone(event)
        self.assertIn('name', event.changes)
        self.assertEqual(event.changes['name'], ['Розы', 'Розы премиум'])

    def test_page_view_is_logged_by_middleware(self):
        self.client.force_login(self.owner)
        self.client.get(reverse('admin:index'))
        self.assertTrue(
            AuditEvent.objects.filter(action=AuditEvent.Action.VIEW, request_path='/admin/').exists()
        )

    def test_login_and_failed_login_are_logged(self):
        self.client.post(reverse('admin:login'), {'username': 'owner', 'password': 'pass12345'})
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN, actor=self.owner).exists())

        self.client.post(reverse('admin:login'), {'username': 'owner', 'password': 'wrong'})
        failed = AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN_FAILED).first()
        self.assertIsNotNone(failed)
        self.assertEqual(failed.outcome, AuditEvent.Outcome.DENIED)

    def test_bulk_action_is_logged(self):
        from reviews.models import Review

        r1 = Review.objects.create(author_name='A', text='x', status=Review.Status.DRAFT)
        r2 = Review.objects.create(author_name='B', text='y', status=Review.Status.DRAFT)
        self.client.force_login(self.owner)
        self.client.post(reverse('admin:reviews_review_changelist'), {
            'action': 'publish_reviews',
            '_selected_action': [str(r1.pk), str(r2.pk)],
        })
        event = AuditEvent.objects.filter(action=AuditEvent.Action.BULK_ACTION).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.context['action'], 'publish_reviews')
        self.assertEqual(event.context['count'], 2)

    def test_object_history_redirects_to_journal_for_privileged_user(self):
        category = Category.objects.create(name='Розы', slug='rozy-h')
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('admin:catalog_category_history', args=[category.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/audit/auditevent/', response['Location'])
        self.assertIn(f'target_object_id={category.pk}', response['Location'])
        # И по этой ссылке changelist открывается без DisallowedModelAdminLookup.
        self.assertEqual(self.client.get(response['Location']).status_code, 200)

    def test_object_history_stays_native_for_user_without_journal_access(self):
        from django.contrib.auth.models import Group, User

        seller = User.objects.create_user('seller_h', password='x', is_staff=True)
        seller.groups.add(Group.objects.get(name=SELLER_SECTION_GROUPS['catalog']))
        category = Category.objects.create(name='Пионы', slug='piony-h')

        self.client.force_login(seller)
        response = self.client.get(
            reverse('admin:catalog_category_history', args=[category.pk])
        )
        self.assertEqual(response.status_code, 200)  # штатная страница истории
