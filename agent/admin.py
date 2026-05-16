from django.contrib import admin
from .models import Repository, ResearchSession, ToolCallLog, Finding


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "url", "last_analyzed_at", "created_at"]
    search_fields = ["name", "url"]
    readonly_fields = ["created_at"]


@admin.register(ResearchSession)
class ResearchSessionAdmin(admin.ModelAdmin):
    list_display = ["id", "repo", "status", "total_tokens_used", "started_at", "completed_at"]
    list_filter = ["status"]
    search_fields = ["question", "repo__name"]
    readonly_fields = ["started_at", "completed_at"]


@admin.register(ToolCallLog)
class ToolCallLogAdmin(admin.ModelAdmin):
    list_display = ["id", "session", "tool_name", "success", "called_at"]
    list_filter = ["tool_name", "success"]
    readonly_fields = ["called_at"]


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ["id", "session", "file_path", "line_reference", "created_at"]
    search_fields = ["file_path", "note"]
    readonly_fields = ["created_at"]
