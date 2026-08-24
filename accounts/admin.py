from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group

from .models import DIRECTOR_GROUP_NAME, SellerAccount


@admin.register(SellerAccount)
class SellerAccountAdmin(UserAdmin):
    """Раздел «Продавцы» — им управляет директор магазина.

    Ключевое отличие от стандартной админки пользователей:
    - список показывает только продавцов, не владельца и не других директоров;
    - права выдаются не произвольными Django-правами, а только галочками по
      готовым разделам (группам) — так директор физически не может выдать
      себе или продавцу доступ шире, чем предполагалось;
    - is_staff/is_superuser не показываются в форме и всегда выставляются
      программно, чтобы через эту форму нельзя было создать ещё одного
      суперпользователя или директора.
    """

    list_display = ('username', 'first_name', 'last_name', 'is_active', 'group_names')
    list_filter = ('is_active', 'groups')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Личные данные', {'fields': ('first_name', 'last_name', 'email')}),
        ('Доступ', {'fields': ('is_active', 'groups')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'first_name', 'last_name', 'email', 'groups'),
        }),
    )
    filter_horizontal = ('groups',)

    @admin.display(description='Доступ к разделам')
    def group_names(self, obj):
        return ', '.join(obj.groups.values_list('name', flat=True)) or '—'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Директор видит только продавцов — не владельца и не других директоров.
        return qs.filter(is_superuser=False).exclude(groups__name=DIRECTOR_GROUP_NAME)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == 'groups' and not request.user.is_superuser:
            kwargs['queryset'] = Group.objects.exclude(name=DIRECTOR_GROUP_NAME)
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        # Через этот раздел учётка всегда становится обычным сотрудником
        # админки без прав суперпользователя — эти поля тут не показываются
        # намеренно, но на случай прямого POST-запроса дублируем проверку и здесь.
        obj.is_staff = True
        obj.is_superuser = False
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        # Как и с товарами/заказами — без удаления, только деактивация.
        return False
