"""
Resume analysis service.

Ported from the standalone app_2.py script. Turns a resume file (PDF, Word,
or image) into a normalized dict of fields that map 1:1 onto Candidate model
fields, using Gemini for extraction.
"""

import io
import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from PIL import Image


class ResumeAnalysisError(Exception):
    """Raised when a resume file can't be parsed or the AI call fails."""


# ==============================================================================
# PROMPT TEMPLATE
# ==============================================================================

EXTRACTION_PROMPT = """
Analyze this resume and extract the following information in valid JSON format.

IMPORTANT RULES:
- Return ONLY valid JSON, no markdown, no explanations, no extra text.
- Use null for missing fields, empty arrays [] for missing lists.
- Extract ALL information visible in the resume.
- For dates, preserve the original format as closely as possible.
- For experiences, extract EACH job separately.

REQUIRED JSON STRUCTURE:
{
    "full_name": string or null,
    "email": string or null,
    "phone": string or null,
    "location": string or null,
    "linkedin_url": string or null,
    "github_url": string or null,
    "current_job_title": string or null,
    "professional_summary": string or null,

    "highest_degree": string or null,
    "field_of_study": string or null,
    "university": string or null,
    "graduation_year": integer or null,
    "gpa": number or null,

    "experiences": [
        {
            "company_name": string,
            "job_title": string,
            "start_date": string,
            "end_date": string,
            "description": string or null
        }
    ],

    "skills": [string],
    "languages": [string],
    "certifications": [string],

    "projects": [
        {
            "name": string,
            "technologies": [string]
        }
    ],

    "candidate_summary": string
}

FIELD INSTRUCTIONS:
- full_name: Complete name of the candidate
- email: Email address
- phone: Phone number with country code if available
- location: City, Country or just Country
- linkedin_url: Full LinkedIn profile URL
- github_url: Full GitHub profile URL
- current_job_title: Most recent or current job title
- professional_summary: The candidate's own summary/objective if present
- highest_degree: e.g., "Bachelor's", "Master's", "PhD", "High School"
- field_of_study: e.g., "Computer Science", "Business Administration"
- university: Name of the educational institution
- graduation_year: Year of graduation as integer (e.g., 2020)
- gpa: Grade Point Average as a number, exactly as stated (e.g., "3.8/4.0" -> 3.8,
  "4.5/5" -> 4.5). Null if not explicitly mentioned - never estimate or guess it.
- experiences: List of ALL work experiences, ordered from most recent to oldest
- skills: Technical and soft skills mentioned
- languages: Spoken/written languages with proficiency if mentioned
- certifications: Professional certifications and courses
- projects: Personal or professional projects with technologies used
- candidate_summary: A concise, honest, 2-3 sentence professional assessment,
  based STRICTLY on what is stated in the resume:
    * Be respectful and neutral in tone - never mocking, dismissive, or harsh.
    * Do not exaggerate strengths or invent qualifications that aren't stated.
    * If experience is limited or information is missing, say so factually and
      matter-of-factly (e.g., "Limited professional experience; strongest
      qualifications are academic.") rather than critically.
    * Do not guess the candidate's fit for any specific job - only summarize
      what their background actually shows.
""".strip()


# ==============================================================================
# LLM ABSTRACTION LAYER
# ==============================================================================

class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def analyze_image(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        """Analyze resume image and return extracted data."""

    @abstractmethod
    def analyze_text(self, text: str) -> dict[str, Any]:
        """Analyze resume text and return extracted data."""


class GeminiProvider(BaseLLMProvider):
    """Google Gemini implementation."""

    def __init__(self):
        from google import genai
        from google.genai import types

        self.types = types

        if not settings.GEMINI_API_KEY:
            raise ResumeAnalysisError("GEMINI_API_KEY not configured in .env.")

        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model = "gemini-2.5-flash"

    def _parse_response(self, response_text: str) -> dict[str, Any]:
        """Parse and validate LLM response."""
        cleaned = response_text.strip()

        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ResumeAnalysisError(
                "Gemini returned a response that wasn't valid JSON."
            ) from exc

    def analyze_image(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        """Analyze resume image using Gemini Vision."""
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    EXTRACTION_PROMPT,
                    self.types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                ],
                config=self.types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:
            raise ResumeAnalysisError(f"Gemini API request failed: {exc}") from exc

        if not response.text:
            raise ResumeAnalysisError("Empty response from Gemini API.")

        return self._parse_response(response.text)

    def analyze_text(self, text: str) -> dict[str, Any]:
        """Analyze resume text using Gemini."""
        full_prompt = f"{EXTRACTION_PROMPT}\n\n---\nRESUME TEXT:\n{text}"

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[full_prompt],
                config=self.types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:
            raise ResumeAnalysisError(f"Gemini API request failed: {exc}") from exc

        if not response.text:
            raise ResumeAnalysisError("Empty response from Gemini API.")

        return self._parse_response(response.text)


def get_llm_provider(provider_name: str = "gemini") -> BaseLLMProvider:
    """Get LLM provider by name."""
    providers = {
        "gemini": GeminiProvider,
        # "openai": OpenAIProvider,  # للمستقبل
    }

    if provider_name not in providers:
        raise ResumeAnalysisError(f"Unknown provider: {provider_name}.")

    return providers[provider_name]()


# ==============================================================================
# FILE PROCESSORS
# ==============================================================================

class FileProcessor:
    """Handle different file types."""

    SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg"}

    @staticmethod
    def process_pdf(file_path: Path) -> tuple[str, list[tuple[bytes, str]]]:
        """Extract text and images from PDF. Returns: (text, [(image_bytes, mime_type)])."""
        import fitz  # PyMuPDF

        text_parts = []
        images = []

        doc = fitz.open(file_path)

        for page in doc:
            text_parts.append(page.get_text())

            for img in page.get_images(full=True):
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                image_ext = base_image["ext"]

                mime_map = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg"}
                mime_type = mime_map.get(image_ext, "image/png")

                images.append((image_bytes, mime_type))

        doc.close()

        return "\n".join(text_parts), images

    @staticmethod
    def process_word(file_path: Path) -> tuple[str, list[tuple[bytes, str]]]:
        """Extract text and images from Word document."""
        from docx import Document

        doc = Document(file_path)

        text_parts = [para.text for para in doc.paragraphs]

        images = []
        for rel in doc.part.rels.values():
            if "image" in rel.target_ref:
                image_bytes = rel.target_part.blob
                content_type = rel.target_part.content_type
                images.append((image_bytes, content_type))

        return "\n".join(text_parts), images

    @staticmethod
    def process_image(file_path: Path) -> tuple[bytes, str]:
        """Load image file. Returns: (image_bytes, mime_type)."""
        with Image.open(file_path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            buffer = io.BytesIO()
            img_format = img.format if img.format in ("PNG", "JPEG") else "PNG"
            img.save(buffer, format=img_format)

            mime_type = "image/png" if img_format == "PNG" else "image/jpeg"

            return buffer.getvalue(), mime_type


# ==============================================================================
# EXPERIENCE CALCULATOR
# ==============================================================================

MONTHS_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def parse_date(date_str: str) -> tuple[int, int] | None:
    """Parse date string to (month, year). Handles "January 2020", "2020", "Present"."""
    if not date_str:
        return None

    date_str = date_str.strip().lower()

    if date_str in {"present", "current", "now", "today", "ongoing"}:
        now = datetime.now()
        return (now.month, now.year)

    match = re.search(r"([a-z]+)\s*(\d{4})", date_str)
    if match:
        month_name = match.group(1)[:3]
        year = int(match.group(2))
        if month_name in MONTHS_MAP:
            return (MONTHS_MAP[month_name], year)

    match = re.search(r"(\d{4})", date_str)
    if match:
        return (1, int(match.group(1)))

    return None


def calculate_experience_months(start: tuple[int, int], end: tuple[int, int]) -> int:
    """Calculate months between two dates (inclusive)."""
    start_month, start_year = start
    end_month, end_year = end

    months = (end_year - start_year) * 12 + (end_month - start_month)
    return max(0, months)


def calculate_total_experience_months(experiences: list[dict]) -> int | None:
    """Calculate total months of experience from experiences list. None if unable to calculate."""
    if not experiences:
        return None

    total_months = 0
    valid_count = 0

    for exp in experiences:
        start = parse_date(exp.get("start_date", ""))
        end = parse_date(exp.get("end_date", ""))

        if start and end:
            total_months += calculate_experience_months(start, end)
            valid_count += 1

    if valid_count == 0:
        return None

    return total_months


# ==============================================================================
# DATA NORMALIZER
# ==============================================================================

def normalize_extracted_data(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate extracted data into Candidate model field names."""

    def safe_str(value) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None

    def safe_int(value) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    def safe_list(value) -> list:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if item]

    def safe_float(value) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    experiences = []
    for exp in raw_data.get("experiences", []) or []:
        if isinstance(exp, dict):
            experiences.append({
                "company_name": safe_str(exp.get("company_name")) or "Unknown",
                "job_title": safe_str(exp.get("job_title")) or "Unknown",
                "start_date": safe_str(exp.get("start_date")),
                "end_date": safe_str(exp.get("end_date")),
                "description": safe_str(exp.get("description")),
            })

    projects = []
    for proj in raw_data.get("projects", []) or []:
        if isinstance(proj, dict):
            projects.append({
                "name": safe_str(proj.get("name")) or "Unknown",
                "technologies": safe_list(proj.get("technologies", [])),
            })

    normalized = {
        "full_name": safe_str(raw_data.get("full_name")),
        "email": safe_str(raw_data.get("email")),
        "phone": safe_str(raw_data.get("phone")),
        "location": safe_str(raw_data.get("location")),
        "linkedin_url": safe_str(raw_data.get("linkedin_url")),
        "github_url": safe_str(raw_data.get("github_url")),
        "current_job_title": safe_str(raw_data.get("current_job_title")),
        "professional_summary": safe_str(raw_data.get("professional_summary")),
        "highest_degree": safe_str(raw_data.get("highest_degree")),
        "field_of_study": safe_str(raw_data.get("field_of_study")),
        "university": safe_str(raw_data.get("university")),
        "graduation_year": safe_int(raw_data.get("graduation_year")),
        "gpa": safe_float(raw_data.get("gpa")),
        "experiences": experiences,
        "skills": safe_list(raw_data.get("skills", [])),
        "languages": safe_list(raw_data.get("languages", [])),
        "certifications": safe_list(raw_data.get("certifications", [])),
        "projects": projects,
        "candidate_summary": safe_str(raw_data.get("candidate_summary")),
    }

    normalized["total_experience_months"] = calculate_total_experience_months(experiences)

    return normalized


# ==============================================================================
# MAIN ANALYZER
# ==============================================================================

class ResumeAnalyzer:
    """Main resume analyzer class."""

    def __init__(self, provider: str = "gemini"):
        self.llm = get_llm_provider(provider)
        self.file_processor = FileProcessor()

    def analyze(self, file_path: str | Path) -> dict[str, Any]:
        """Analyze a resume file (PDF, Image, or Word). Returns normalized field dict."""
        file_path = Path(file_path)

        if not file_path.exists():
            raise ResumeAnalysisError(f"File not found: {file_path}")

        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            text, images = self.file_processor.process_pdf(file_path)

            # Prefer text: a PDF with a profile photo next to real text should
            # not have its text thrown away in favor of analyzing the photo.
            # Image analysis is only a fallback for scanned, text-less PDFs.
            if text.strip():
                raw_data = self.llm.analyze_text(text)
            elif images:
                raw_data = self.llm.analyze_image(images[0][0], images[0][1])
            else:
                raise ResumeAnalysisError("PDF contains no extractable content.")

        elif suffix == ".docx":
            text, images = self.file_processor.process_word(file_path)

            if text.strip():
                raw_data = self.llm.analyze_text(text)
            elif images:
                raw_data = self.llm.analyze_image(images[0][0], images[0][1])
            else:
                raise ResumeAnalysisError("Word document contains no extractable content.")

        elif suffix in self.file_processor.SUPPORTED_IMAGES:
            image_bytes, mime_type = self.file_processor.process_image(file_path)
            raw_data = self.llm.analyze_image(image_bytes, mime_type)

        else:
            raise ResumeAnalysisError(f"Unsupported file type: {suffix}")

        return normalize_extracted_data(raw_data)
