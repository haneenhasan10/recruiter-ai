from django.db import models


class Candidate(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        SHORTLISTED = "shortlisted", "Shortlisted"
        REJECTED = "rejected", "Rejected"
        HIRED = "hired", "Hired"

    class Source(models.TextChoices):
        INTERNAL = "internal", "HR Upload"
        PUBLIC = "public", "Careers Page"

    # The original uploaded file, kept so an HR user can open it later
    resume_file = models.FileField(upload_to="resumes/%Y/%m/")
    # SHA-256 of the file content, used to block re-uploading the same file
    file_hash = models.CharField(max_length=64, unique=True, blank=True, null=True)

    # Basic info
    full_name = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    location = models.CharField(max_length=255, blank=True, null=True)
    linkedin_url = models.URLField(blank=True, null=True)
    github_url = models.URLField(blank=True, null=True)
    current_job_title = models.CharField(max_length=255, blank=True, null=True)
    professional_summary = models.TextField(blank=True, null=True)

    # Education
    highest_degree = models.CharField(max_length=100, blank=True, null=True)
    field_of_study = models.CharField(max_length=255, blank=True, null=True)
    university = models.CharField(max_length=255, blank=True, null=True)
    graduation_year = models.IntegerField(blank=True, null=True)
    gpa = models.FloatField(blank=True, null=True)

    # Nested/list data stored as JSON (see chat for why)
    experiences = models.JSONField(default=list, blank=True)
    skills = models.JSONField(default=list, blank=True)
    languages = models.JSONField(default=list, blank=True)
    certifications = models.JSONField(default=list, blank=True)
    projects = models.JSONField(default=list, blank=True)

    # AI-generated fields
    candidate_summary = models.TextField(blank=True, null=True)
    total_experience_months = models.IntegerField(blank=True, null=True)

    # HR workflow
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.NEW
    )
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.INTERNAL
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.full_name or f"Candidate #{self.pk}"

    @property
    def experience_display(self) -> str | None:
        """Human-readable experience, e.g. '5 mos', '1 yr 2 mos', '3 yrs'."""
        if self.total_experience_months is None:
            return None

        years, months = divmod(self.total_experience_months, 12)
        parts = []
        if years:
            parts.append(f"{years} yr{'s' if years != 1 else ''}")
        if months or not years:
            parts.append(f"{months} mo{'s' if months != 1 else ''}")
        return " ".join(parts)


class JobDescription(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class Match(models.Model):
    job = models.ForeignKey(
        JobDescription, on_delete=models.CASCADE, related_name="matches"
    )
    candidate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name="matches"
    )
    score = models.IntegerField()
    explanation = models.TextField()
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-score"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "candidate"], name="unique_job_candidate_match"
            )
        ]

    def __str__(self) -> str:
        return f"{self.candidate} vs {self.job} = {self.score}%"
