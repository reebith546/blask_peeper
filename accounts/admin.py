from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import Group, User

from audit.admin_mixins import AuditModelAdmin
from audit.services import record

from .models import (
    DIRECTOR_GROUP_NAME,
    ROLE_CHOICES,
    ROLE_DIRECTOR,
    ROLE_SELLER,
    SELLER_SECTION_GROUPS,
    SellerAccount,
)

# Разделы для продавца: код -> подпись на галочке.
SECTION_CHOICES = tuple(
    (code, name.replace('Продавец: ', '')) for code, name in SELLER_SECTION_GROUPS.items()
)


class _TeamRoleFormMixin(forms.ModelForm):
    """Общая логика форм создания/редактирования сотрудника: поле «Роль»,
    галочки разделов и проверки инвариантов (самоблокировка, последний директор)."""

    role = forms.ChoiceField(
        label='Роль',
        choices=ROLE_CHOICES,
        initial=ROLE_SELLER,
        widget=forms.RadioSelect,
        help_text='Продавец — доступ только к отмеченным разделам. '
                  'Директор — полный доступ и раздел «Сотрудники».',
    )
    sections = forms.MultipleChoiceField(
        label='Разделы продавца',
        choices=SECTION_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Только для роли «Продавец».',
    )

    # Проставляется в AccountAdmin.get_form — нужно для проверки самоблокировки.
    _request_user = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Требования к паролю и так проверяются валидаторами — в подсказке
        # оставляем одну короткую строку вместо простыни из четырёх.
        for name in ('password1', 'password2'):
            if name in self.fields:
                self.fields[name].help_text = (
                    'Минимум 8 символов, не только цифры, не похож на имя и логин.'
                ) if name == 'password1' else ''

        if self.instance and self.instance.pk:
            current = self.instance.groups.values_list('name', flat=True)
            if DIRECTOR_GROUP_NAME in current:
                self.fields['role'].initial = ROLE_DIRECTOR
            self.fields['sections'].initial = [
                code for code, group_name in SELLER_SECTION_GROUPS.items() if group_name in current
            ]

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        is_active = cleaned.get('is_active', True if not self.instance.pk else self.instance.is_active)

        if role == ROLE_SELLER and not cleaned.get('sections'):
            self.add_error('sections', 'Выберите хотя бы один раздел для продавца.')

        editing_self = (
            self._request_user is not None
            and self.instance.pk
            and self.instance.pk == self._request_user.pk
        )
        if editing_self and not is_active:
            self.add_error('is_active', 'Нельзя деактивировать собственную учётную запись.')

        if self.instance.pk:
            was_director = self.instance.groups.filter(name=DIRECTOR_GROUP_NAME).exists()
            stays_active_director = is_active and role == ROLE_DIRECTOR
            other_active_director = (
                User.objects.filter(is_active=True, groups__name=DIRECTOR_GROUP_NAME)
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if was_director and not stays_active_director and not other_active_director:
                raise forms.ValidationError(
                    'Это последний активный директор магазина — сначала назначьте или '
                    'активируйте другого директора.'
                )
        return cleaned


class TeamAccountCreationForm(_TeamRoleFormMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = SellerAccount
        fields = ('username',)


class TeamAccountChangeForm(_TeamRoleFormMixin, UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = SellerAccount


@admin.register(SellerAccount)
class SellerAccountAdmin(AuditModelAdmin, UserAdmin):
    """Раздел «Сотрудники» — им управляет директор магазина (и владелец)."""

    form = TeamAccountChangeForm
    add_form = TeamAccountCreationForm
    # Штатный шаблон UserAdmin показывает англоязычную подсказку про «два шага».
    # У нас учётка создаётся за один шаг (роль и разделы прямо в форме) — берём
    # обычный шаблон формы.
    add_form_template = None

    list_display = ('username', 'first_name', 'last_name', 'role_display', 'is_active')
    list_filter = ('is_active', 'groups')
    ordering = ('username',)

    fieldsets = (
        ('Учётные данные', {'fields': ('username', 'password')}),
        ('Роль и доступ', {
            'fields': ('role', 'sections', 'is_active'),
            'description': 'Роль определяет, что сотрудник видит в админке. '
                           'Продавцу отметьте разделы; директору разделы не нужны.',
        }),
        ('Личные данные', {'fields': ('first_name', 'last_name', 'email')}),
    )
    add_fieldsets = (
        ('Учётные данные', {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2'),
        }),
        ('Роль и доступ', {
            'fields': ('role', 'sections'),
            'description': 'Продавцу отметьте разделы; директору разделы не нужны.',
        }),
        ('Личные данные', {'fields': ('first_name', 'last_name', 'email')}),
    )
    filter_horizontal = ()

    class Media:
        js = ('admin/js/team_role.js',)

    @admin.display(description='Роль')
    def role_display(self, obj):
        return 'Директор магазина' if obj.groups.filter(name=DIRECTOR_GROUP_NAME).exists() else 'Продавец'

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        form_class._request_user = request.user
        return form_class

    def get_queryset(self, request):
        # Владельцы (superuser) в этот раздел не попадают ни для кого — их
        # заводит только владелец через штатный раздел «Пользователи».
        return super().get_queryset(request).filter(is_superuser=False)

    def save_model(self, request, obj, form, change):
        # Через этот раздел учётка всегда — штатный сотрудник админки без
        # прав суперпользователя. Полей в форме нет намеренно; дублируем и здесь.
        obj.is_staff = True
        obj.is_superuser = False
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        before = sorted(form.instance.groups.values_list('name', flat=True)) if change else []
        super().save_related(request, form, formsets, change)

        role = form.cleaned_data.get('role', ROLE_SELLER)
        if role == ROLE_DIRECTOR:
            target = Group.objects.filter(name=DIRECTOR_GROUP_NAME)
        else:
            wanted = [SELLER_SECTION_GROUPS[c] for c in form.cleaned_data.get('sections', [])]
            target = Group.objects.filter(name__in=wanted)
        form.instance.groups.set(target)

        after = sorted(form.instance.groups.values_list('name', flat=True))
        if before != after:
            record(
                action='update',
                actor=request.user,
                target=form.instance,
                changes={'groups': [before, after]},
                context={'account': True, 'sensitive_fields': ['groups'], 'role': role,
                         'via': 'раздел «Сотрудники»'},
                request=request,
            )

    def has_delete_permission(self, request, obj=None):
        # Как и с товарами/заказами — без удаления, только деактивация.
        return False
