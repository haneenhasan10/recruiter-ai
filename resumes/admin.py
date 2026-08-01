from django.contrib import admin

from .models import Candidate, JobDescription, Match


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


@admin.register(JobDescription)
class JobDescriptionAdmin(admin.ModelAdmin):
    list_display = ("title", "created_at")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ("candidate", "job", "score", "computed_at")
    list_filter = ("job",)
    readonly_fields = ("computed_at",)
