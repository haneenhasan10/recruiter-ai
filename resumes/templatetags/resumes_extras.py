from django import template

register = template.Library()


@register.filter
def join_list(value, sep=", "):
    """Render a plain list of strings (skills, languages, ...) as text."""
    if not value:
        return "-"
    return sep.join(str(item) for item in value)


@register.filter
def experience_summary(experiences):
    """Render Candidate.experiences (list of dicts) as one compact line."""
    if not experiences:
        return "-"
    parts = [
        f"{exp.get('job_title', '')} @ {exp.get('company_name', '')}"
        for exp in experiences
    ]
    return " | ".join(parts)


@register.filter
def project_summary(projects):
    """Render Candidate.projects (list of dicts) as one compact line."""
    if not projects:
        return "-"
    return " | ".join(proj.get("name", "") for proj in projects)


STATUS_BADGE_CLASSES = {
    "new": "badge-status-new",
    "shortlisted": "badge-status-shortlisted",
    "rejected": "badge-status-rejected",
    "hired": "badge-status-hired",
}


@register.filter
def status_badge_class(status):
    """CSS class for a candidate's status badge (see style.css)."""
    return STATUS_BADGE_CLASSES.get(status, "badge-status-new")
