from django.contrib import admin

from .models import Candidate


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "current_job_title",
        "experience_display",
        "status",
        "created_at",
    )
    list_filter = ("status", "highest_degree")
    search_fields = ("full_name", "email", "current_job_title", "skills")
    readonly_fields = ("created_at", "updated_at")
