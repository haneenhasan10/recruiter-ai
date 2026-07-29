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
]
