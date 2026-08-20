"""Loading the uploaded corpus.

The executor uploads a death certificate, twelve months of statements, and a pile of
phone photos of physical mail. Two things arrive per document: page images, and the text
layer.

In cloud mode Gemini reads the images directly - that is the multimodal path, and the
text layer is only a fallback for pages it cannot render. In local mode there is no
vision model, so the corpus ships with its text layer already extracted and the loader
says so rather than pretending a photo was parsed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from packages.core.config import get_settings
from packages.core.logging import get_logger

log = get_logger("discovery.documents")

TEXT_SUFFIXES = {".txt", ".md"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class SourceDocument:
    name: str
    text: str
    kind: str = "statement"  # statement | letter | certificate | photo
    pages: int = 1
    images: list[bytes] = field(default_factory=list)
    path: Path | None = None

    @property
    def has_images(self) -> bool:
        return bool(self.images)

    @property
    def provenance(self) -> str:
        return "page images + text layer" if self.has_images else "text layer only"


def classify_kind(name: str) -> str:
    lowered = name.lower()
    if "certificate" in lowered or "death-cert" in lowered:
        return "certificate"
    if "letter" in lowered or "notice" in lowered or "mail" in lowered:
        return "letter"
    if "photo" in lowered or "scan" in lowered:
        return "photo"
    return "statement"


def load_corpus(directory: Path, *, with_images: bool | None = None) -> list[SourceDocument]:
    """Read every document in a directory.

    A document is a text file; an image beside it with the same stem is treated as its
    page image. Images are loaded only in cloud mode - there is no point paying to read
    a JPEG into memory when nothing downstream can look at it.
    """
    settings = get_settings()
    if with_images is None:
        with_images = settings.is_cloud

    documents: list[SourceDocument] = []
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        images: list[bytes] = []
        if with_images:
            for suffix in IMAGE_SUFFIXES:
                candidate = path.with_suffix(suffix)
                if candidate.exists():
                    images.append(candidate.read_bytes())
        documents.append(
            SourceDocument(
                name=path.name,
                text=text,
                kind=classify_kind(path.name),
                pages=max(1, text.count("=== PAGE")),
                images=images,
                path=path,
            )
        )

    log.info(
        "corpus.loaded",
        directory=str(directory),
        documents=len(documents),
        with_images=with_images,
    )
    return documents
