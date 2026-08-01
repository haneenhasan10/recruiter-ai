from django.urls import path

from . import views

app_name = "resumes"

urlpatterns = [
    path("", views.upload_resume, name="upload"),
    path("candidates/", views.candidate_list, name="candidate_list"),
    path("candidates/<int:pk>/", views.candidate_detail, name="candidate_detail"),
    path("candidates/<int:pk>/resume/", views.candidate_resume, name="candidate_resume"),
    path("candidates/<int:pk>/delete/", views.candidate_delete, name="candidate_delete"),
    path("candidates/bulk-delete/", views.candidate_bulk_delete, name="candidate_bulk_delete"),
    path("careers/apply/", views.public_submit_resume, name="public_submit"),
    path("jobs/", views.job_list, name="job_list"),
    path("jobs/new/", views.job_create, name="job_create"),
    path("jobs/<int:pk>/", views.job_detail, name="job_detail"),
    path("jobs/<int:pk>/match/", views.job_run_matching, name="job_run_matching"),
    path("jobs/<int:pk>/delete/", views.job_delete, name="job_delete"),
]
