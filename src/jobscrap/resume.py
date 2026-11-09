"""Extract a resume locally and expose normalized text plus its scoring fingerprint."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import re

from .domain import digest

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resume:
    """In-memory resume content; only its fingerprint is persisted as metadata."""
    text: str
    content_hash: str

    @classmethod
    def from_text(cls, text: str):
        """Ignore formatting-only changes so they do not trigger paid rescoring."""
        text = " ".join(text.split())
        if len(text) < 100:
            raise ValueError("Resume has insufficient selectable text; provide a text-based PDF")
        return cls(text, digest(text))


def load_resume(path: Path) -> Resume:
    """Read selectable PDF text; scanned/encrypted documents need user preparation."""
    from pypdf import PdfReader

    if not path.is_file():
        raise ValueError("Resume PDF not found; check RESUME_PATH")
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("Resume PDF is encrypted; provide an unlocked copy")
    resume = Resume.from_text("\n".join(page.extract_text() or "" for page in reader.pages))
    log.debug("resume_loaded pages=%s characters=%s", len(reader.pages), len(resume.text))
    return resume


def redact_contacts(text: str) -> str:
    """Reduce unnecessary contact data sent to an optional external scoring provider."""
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    text = re.sub(r"https?://\S+", "[link]", text)
    return re.sub(r"(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)", "[phone]", text)
