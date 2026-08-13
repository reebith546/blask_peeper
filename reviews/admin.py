from django.contrib import admin

from .models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('author_name', 'product', 'rating', 'status', 'created_at')
    list_editable = ('status',)
    list_filter = ('status', 'rating')
    search_fields = ('author_name', 'text')
    actions = ['publish_reviews']

    @admin.action(description='Опубликовать выбранные отзывы')
    def publish_reviews(self, request, queryset):
        queryset.update(status=Review.Status.PUBLISHED)
