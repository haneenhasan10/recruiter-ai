from pathlib import Path

from django import forms
from django.contrib.auth.forms import AuthenticationForm

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".png", ".jpg", ".jpeg"}
MAX_UPLOAD_SIZE_MB = 10
MAX_FILES_PER_UPLOAD = 50


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """A FileField whose widget accepts several files, cleaned as a list."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean

        if not data:
            data = []
        elif not isinstance(data, list):
            data = [data]

        if self.required and not data:
            raise forms.ValidationError(self.error_messages["required"], code="required")

        return [single_file_clean(item, initial) for item in data]


class ResumeUploadForm(forms.Form):
    resume_files = MultipleFileField(label="Resume files (you can select more than one)")

    def clean_resume_files(self):
        files = self.cleaned_data["resume_files"]

        if len(files) > MAX_FILES_PER_UPLOAD:
            raise forms.ValidationError(
                f"You can upload at most {MAX_FILES_PER_UPLOAD} files per batch "
                f"(you selected {len(files)})."
            )

        max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
        for file in files:
            extension = Path(file.name).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                raise forms.ValidationError(
                    f"Unsupported file type: {file.name} ({extension}). "
                    "Allowed types: PDF, Word (.docx), PNG, JPG."
                )
            if file.size > max_bytes:
                raise forms.ValidationError(
                    f"File too large: {file.name}. Maximum size is {MAX_UPLOAD_SIZE_MB}MB."
                )

        return files


class StyledAuthenticationForm(AuthenticationForm):
    """AuthenticationForm with Bootstrap classes on its widgets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"class": "form-control"})
        self.fields["password"].widget.attrs.update({"class": "form-control"})
