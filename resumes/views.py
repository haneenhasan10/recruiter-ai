import hashlib

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import ResumeUploadForm
from .models import Candidate
from .services import ResumeAnalysisError, ResumeAnalyzer

# Columns the user can show/hide on the candidate table. "Name" itself is
# always shown and isn't included here - see candidate_list.html.
TOGGLEABLE_COLUMNS = [
    {"key": "contact", "label": "Contact", "default_visible": True},
    {"key": "job_title", "label": "Current Job Title", "default_visible": True},
    {"key": "summary", "label": "Professional Summary", "default_visible": False},
    {"key": "education", "label": "Education", "default_visible": False},
    {"key": "experience", "label": "Experience", "default_visible": True},
    {"key": "experiences", "label": "Work History", "default_visible": False},
    {"key": "projects", "label": "Projects", "default_visible": False},
    {"key": "skills", "label": "Skills", "default_visible": True},
    {"key": "languages", "label": "Languages", "default_visible": False},
    {"key": "certifications", "label": "Certifications", "default_visible": False},
    {"key": "ai_summary", "label": "AI Assessment", "default_visible": False},
    {"key": "status", "label": "Status", "default_visible": True},
    {"key": "uploaded", "label": "Uploaded", "default_visible": True},
    {"key": "file", "label": "File", "default_visible": False},
]


@login_required
def upload_resume(request):
    if request.method == "POST":
        form = ResumeUploadForm(request.POST, request.FILES)

        if form.is_valid():
            files = form.cleaned_data["resume_files"]

            try:
                analyzer = ResumeAnalyzer(provider="gemini")
            except ResumeAnalysisError as exc:
                messages.error(request, f"Could not start the AI analyzer: {exc}")
                return redirect("resumes:upload")

            created_ids = []
            failures = []

            for file in files:
                file_hash = hashlib.sha256(file.read()).hexdigest()
                file.seek(0)

                duplicate = Candidate.objects.filter(file_hash=file_hash).first()
                if duplicate:
                    failures.append(
                        f"{file.name}: duplicate of already-uploaded "
                        f"'{duplicate.full_name or duplicate.pk}' - skipped"
                    )
                    continue

                candidate = Candidate.objects.create(
                    resume_file=file, file_hash=file_hash
                )

                try:
                    extracted_data = analyzer.analyze(candidate.resume_file.path)
                except ResumeAnalysisError as exc:
                    candidate.delete()
                    failures.append(f"{file.name}: {exc}")
                    continue

                for field_name, value in extracted_data.items():
                    setattr(candidate, field_name, value)
                candidate.save()
                created_ids.append(candidate.pk)

            if created_ids:
                messages.success(
                    request, f"Successfully analyzed {len(created_ids)} resume(s)."
                )
            for failure in failures:
                messages.error(request, f"Failed to analyze {failure}")

            if created_ids:
                ids_param = ",".join(str(pk) for pk in created_ids)
                return redirect(f"{reverse('resumes:candidate_list')}?batch={ids_param}")
            return redirect("resumes:upload")
    else:
        form = ResumeUploadForm()

    return render(request, "resumes/upload.html", {"form": form})


@login_required
def candidate_list(request):
    candidates = Candidate.objects.all()

    batch_param = request.GET.get("batch", "")
    showing_batch = False
    if batch_param:
        try:
            batch_ids = [int(pk) for pk in batch_param.split(",") if pk]
            candidates = candidates.filter(pk__in=batch_ids)
            showing_batch = True
        except ValueError:
            pass

    status = request.GET.get("status", "")
    if status:
        candidates = candidates.filter(status=status)

    min_experience = request.GET.get("min_experience", "")
    if min_experience:
        try:
            candidates = candidates.filter(
                total_experience_months__gte=int(min_experience) * 12
            )
        except ValueError:
            pass

    query = request.GET.get("q", "").strip().lower()
    if query:
        candidates = [
            c
            for c in candidates
            if query in (c.full_name or "").lower()
            or query in (c.email or "").lower()
            or query in (c.current_job_title or "").lower()
            or any(query in skill.lower() for skill in c.skills)
        ]

    context = {
        "candidates": candidates,
        "showing_batch": showing_batch,
        "status_choices": Candidate.Status.choices,
        "selected_status": status,
        "min_experience": min_experience,
        "query": request.GET.get("q", ""),
        "toggleable_columns": TOGGLEABLE_COLUMNS,
    }
    return render(request, "resumes/candidate_list.html", context)


@login_required
def candidate_detail(request, pk):
    candidate = get_object_or_404(Candidate, pk=pk)

    if request.method == "POST":
        new_status = request.POST.get("status")
        if new_status in Candidate.Status.values:
            candidate.status = new_status
            candidate.save(update_fields=["status", "updated_at"])
            messages.success(request, f"Status updated to {candidate.get_status_display()}.")
        return redirect("resumes:candidate_detail", pk=candidate.pk)

    context = {"candidate": candidate, "status_choices": Candidate.Status.choices}
    return render(request, "resumes/candidate_detail.html", context)


@login_required
def candidate_resume(request, pk):
    """Stream the original uploaded resume file - the only way to reach it."""
    candidate = get_object_or_404(Candidate, pk=pk)
    filename = candidate.resume_file.name.rsplit("/", 1)[-1]
    return FileResponse(
        candidate.resume_file.open("rb"), as_attachment=True, filename=filename
    )


@login_required
def candidate_delete(request, pk):
    candidate = get_object_or_404(Candidate, pk=pk)
    if request.method == "POST":
        name = candidate.full_name or f"Candidate #{candidate.pk}"
        candidate.resume_file.delete(save=False)
        candidate.delete()
        messages.success(request, f"Deleted {name}.")
    return redirect("resumes:candidate_list")


@login_required
def candidate_bulk_delete(request):
    if request.method == "POST":
        ids = request.POST.getlist("candidate_ids")
        candidates = Candidate.objects.filter(pk__in=ids)
        count = candidates.count()
        for candidate in candidates:
            candidate.resume_file.delete(save=False)
        candidates.delete()
        messages.success(request, f"Deleted {count} candidate(s).")
    return redirect("resumes:candidate_list")
