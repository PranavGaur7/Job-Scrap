from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .domain import digest


@dataclass(frozen=True)
class Resume:
    text: str
    content_hash: str

    @classmethod
    def from_text(cls, text: str):
        text = " ".join(text.split())
        if len(text) < 100:
            raise ValueError("Resume has insufficient selectable text; provide a text-based PDF")
        return cls(text, digest(text))


def load_resume(path: Path) -> Resume:
    from pypdf import PdfReader

    if not path.is_file():
        raise ValueError("Resume PDF not found; check RESUME_PATH")
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("Resume PDF is encrypted; provide an unlocked copy")
    return Resume.from_text("\n".join(page.extract_text() or "" for page in reader.pages))


def redact_contacts(text: str) -> str:
    """Reduce unnecessary contact data sent to an optional external scoring provider."""
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    text = re.sub(r"https?://\S+", "[link]", text)
    return re.sub(r"(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)", "[phone]", text)
