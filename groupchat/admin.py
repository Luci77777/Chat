from django.contrib import admin

from .models import ChatGroup, GroupMembership, GroupMessage


class GroupMembershipInline(admin.TabularInline):
    model = GroupMembership
    extra = 0


@admin.register(ChatGroup)
class ChatGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'created_at', 'member_count')
    inlines = [GroupMembershipInline]


@admin.register(GroupMessage)
class GroupMessageAdmin(admin.ModelAdmin):
    list_display = ('group', 'sender', 'kind', 'created_at')
    list_filter = ('kind',)
