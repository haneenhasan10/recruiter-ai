import hashlib
import tempfile
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import IntegrityError
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .forms import ALLOWED_EXTENSIONS, MAX_UPLOAD_SIZE_MB, ResumeUploadForm
from .models import Candidate, JobDescription, Match
from .services import ResumeAnalysisError, ResumeAnalyzer, build_candidate_profile

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
    {"key": "source", "label": "Source", "default_visible": False},
    {"key": "uploaded", "label": "Uploaded", "default_visible": True},
    {"key": "file", "label": "File", "default_visible": False},
]


def _process_upload(file, analyzer, source=Candidate.Source.INTERNAL):
    """Analyze one uploaded file and create a Candidate if it succeeds.

    Returns (candidate_or_none, error_message_or_none). No Candidate row is
    created unless analysis actually succeeds, and duplicate file content is
    rejected before any AI call is made.
    """
    file_hash = hashlib.sha256(file.read()).hexdigest()
    file.seek(0)

    duplicate = Candidate.objects.filter(file_hash=file_hash).first()
    if duplicate:
        return None, f"duplicate of already-uploaded '{duplicate.full_name or duplicate.pk}'"

    suffix = Path(file.name).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        for chunk in file.chunks():
            tmp.write(chunk)
        tmp.flush()
        file.seek(0)

        try:
            extracted_data = analyzer.analyze(tmp.name)
        except ResumeAnalysisError as exc:
            return None, str(exc)

    try:
        candidate = Candidate.objects.create(
            resume_file=file, file_hash=file_hash, source=source, **extracted_data
        )
    except IntegrityError:
        # Same file finished analyzing in a parallel chunk a moment earlier
        # and won the race to save first - treat it the same as a duplicate.
        duplicate = Candidate.objects.filter(file_hash=file_hash).first()
        name = duplicate.full_name or duplicate.pk if duplicate else "another upload"
        return None, f"duplicate of already-uploaded '{name}'"

    return candidate, None


@login_required
def upload_resume(request):
    # The upload page auto-splits large selections into small chunks and
    # posts each one here via fetch() - see resumes/upload.html. Those
    # requests carry this header and get a small JSON reply instead of a
    # full page redirect, so no single HTTP request ever has to process
    # more than a couple dozen resumes (and risk hitting the server timeout).
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if request.method == "POST":
        form = ResumeUploadForm(request.POST, request.FILES)

        if form.is_valid():
            files = form.cleaned_data["resume_files"]

            try:
                analyzer = ResumeAnalyzer(provider="gemini")
            except ResumeAnalysisError as exc:
                if is_ajax:
                    return JsonResponse(
                        {"error": f"Could not start the AI analyzer: {exc}"},
                        status=400,
                    )
                messages.error(request, f"Could not start the AI analyzer: {exc}")
                return redirect("resumes:upload")

            created_ids = []
            failures = []

            for file in files:
                candidate, error = _process_upload(file, analyzer)
                if candidate:
                    created_ids.append(candidate.pk)
                else:
                    failures.append(f"{file.name}: {error}")

            if is_ajax:
                return JsonResponse({"created_ids": created_ids, "failures": failures})

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

        if is_ajax:
            errors = form.errors.get("resume_files") or ["Invalid upload."]
            return JsonResponse({"error": "; ".join(errors)}, status=400)
    else:
        form = ResumeUploadForm()

    return render(request, "resumes/upload.html", {"form": form})


PUBLIC_RATE_LIMIT_MAX = 5
PUBLIC_RATE_LIMIT_WINDOW_SECONDS = 3600


@csrf_exempt
@require_http_methods(["GET", "POST"])
def public_submit_resume(request):
    """Public, login-free endpoint a company careers page can POST a resume to."""
    if request.method == "GET":
        return render(request, "resumes/public_apply.html")

    client_ip = request.META.get("REMOTE_ADDR", "unknown")
    cache_key = f"public_submit:{client_ip}"
    attempts = cache.get(cache_key, 0)
    if attempts >= PUBLIC_RATE_LIMIT_MAX:
        return render(
            request,
            "resumes/public_submit_result.html",
            {"success": False, "message": "Too many submissions. Please try again later."},
        )
    cache.set(cache_key, attempts + 1, PUBLIC_RATE_LIMIT_WINDOW_SECONDS)

    uploaded_file = request.FILES.get("resume_file")
    if not uploaded_file:
        return render(
            request,
            "resumes/public_submit_result.html",
            {"success": False, "message": "No file was received. Please attach your resume."},
        )

    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return render(
            request,
            "resumes/public_submit_result.html",
            {
                "success": False,
                "message": "Unsupported file type. Please upload a PDF, Word (.docx), PNG, or JPG file.",
            },
        )

    if uploaded_file.size > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        return render(
            request,
            "resumes/public_submit_result.html",
            {"success": False, "message": f"File too large. Maximum size is {MAX_UPLOAD_SIZE_MB}MB."},
        )

    try:
        analyzer = ResumeAnalyzer(provider="gemini")
    except ResumeAnalysisError:
        return render(
            request,
            "resumes/public_submit_result.html",
            {"success": False, "message": "We're unable to process applications right now. Please try again later."},
        )

    candidate, error = _process_upload(
        uploaded_file, analyzer, source=Candidate.Source.PUBLIC
    )

    if candidate is None and error and "duplicate" in error:
        # Already have this exact file - treat as a friendly success, not an error.
        return render(
            request,
            "resumes/public_submit_result.html",
            {"success": True, "message": "We already have your application on file. Thank you!"},
        )

    if candidate is None:
        return render(
            request,
            "resumes/public_submit_result.html",
            {
                "success": False,
                "message": "We couldn't process your file. Please make sure it's a valid, readable PDF, Word document, or image and try again.",
            },
        )

    return render(
        request,
        "resumes/public_submit_result.html",
        {"success": True, "message": "Thank you! Your application has been received."},
    )


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


@login_required
def job_list(request):
    jobs = JobDescription.objects.all()
    return render(request, "resumes/job_list.html", {"jobs": jobs})


@login_required
def job_create(request):
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        if not title or not description:
            messages.error(request, "Both title and description are required.")
        else:
            job = JobDescription.objects.create(title=title, description=description)
            messages.success(request, f"Job '{job.title}' created.")
            return redirect("resumes:job_detail", pk=job.pk)
    return render(request, "resumes/job_form.html")


@login_required
def job_detail(request, pk):
    job = get_object_or_404(JobDescription, pk=pk)
    matches = job.matches.select_related("candidate").order_by("-score")
    return render(request, "resumes/job_detail.html", {"job": job, "matches": matches})


@login_required
def job_run_matching(request, pk):
    job = get_object_or_404(JobDescription, pk=pk)

    if request.method == "POST":
        try:
            analyzer = ResumeAnalyzer(provider="gemini")
        except ResumeAnalysisError as exc:
            messages.error(request, f"Could not start the AI analyzer: {exc}")
            return redirect("resumes:job_detail", pk=job.pk)

        matched_count = 0
        failed_count = 0

        for candidate in Candidate.objects.all():
            profile = build_candidate_profile(candidate)
            try:
                result = analyzer.llm.match_candidate(
                    job.title, job.description, profile
                )
            except ResumeAnalysisError:
                failed_count += 1
                continue

            Match.objects.update_or_create(
                job=job,
                candidate=candidate,
                defaults={
                    "score": result["score"],
                    "explanation": result["explanation"],
                },
            )
            matched_count += 1

        messages.success(
            request, f"Matched {matched_count} candidate(s) against '{job.title}'."
        )
        if failed_count:
            messages.error(request, f"{failed_count} candidate(s) could not be matched.")

    return redirect("resumes:job_detail", pk=job.pk)


@login_required
def job_delete(request, pk):
    job = get_object_or_404(JobDescription, pk=pk)
    if request.method == "POST":
        title = job.title
        job.delete()
        messages.success(request, f"Deleted job '{title}'.")
    return redirect("resumes:job_list")
