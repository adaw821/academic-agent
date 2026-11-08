"""Convert PowerPoint files to PDF for in-browser preview.

Two render engines are tried in order:

1. LibreOffice (``soffice``) headless — cross-platform, no user profile hassle.
2. Microsoft PowerPoint via COM automation — Windows only, used when LibreOffice
   is missing but Office is installed (``pywin32`` required).

The resulting PDF is stored next to the original file (same stem, ``.pdf``
suffix) so the existing PDF viewer can render it. Conversion is triggered
lazily by the preview endpoint (``?preview=1``), never by the parsing pipeline.
If no engine is available or conversion fails, the frontend falls back to the
original PowerPoint file (``pptx-preview``) or to the parsed text.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger("uvicorn.error")

PPT_EXTS = {".ppt", ".pptx"}

# PowerPoint's PpSaveAsFileType.ppSaveAsPDF
_PP_SAVE_AS_PDF = 32

# Locations checked in order when SOFFICE_PATH is not set.
_DEFAULT_SOFFICE_PATHS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/opt/libreoffice/program/soffice",
]


def find_soffice() -> Path | None:
    """Resolve the soffice executable, preferring the SOFFICE_PATH env var."""
    env_path = os.environ.get("SOFFICE_PATH")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return candidate
        logger.warning("SOFFICE_PATH set but file not found: %s", env_path)

    for candidate in _DEFAULT_SOFFICE_PATHS:
        path = Path(candidate)
        if path.is_file():
            return path

    on_path = shutil.which("soffice")
    if on_path:
        return Path(on_path)
    return None


def convert_to_pdf(ppt_path: Path, *, timeout: int = 120) -> Path | None:
    """Convert ``ppt_path`` to a sibling PDF. Returns the PDF path or None."""
    if ppt_path.suffix.lower() not in PPT_EXTS:
        return None

    pdf_path = ppt_path.with_suffix(".pdf")
    # Remove any stale output first so we don't mistake it for a fresh result.
    pdf_path.unlink(missing_ok=True)

    if find_soffice() is not None:
        converted = _convert_with_soffice(ppt_path, pdf_path, timeout=timeout)
        if converted is not None:
            return converted

    # LibreOffice missing/failed → try the local PowerPoint installation.
    return _convert_with_powerpoint(ppt_path, pdf_path)


def _convert_with_soffice(ppt_path: Path, pdf_path: Path, *, timeout: int) -> Path | None:
    soffice = find_soffice()
    if soffice is None:
        return None

    # Isolate the LibreOffice user profile to avoid clashes with other running
    # instances (soffice refuses to start if its profile is locked).
    profile_dir = Path(tempfile.gettempdir()) / f"lo_profile_{uuid4().hex}"
    profile_url = profile_dir.as_uri()
    if sys.platform.startswith("win"):
        # file:///C:/... form expected on Windows.
        profile_url = "file:///" + str(profile_dir).replace("\\", "/")

    cmd = [
        str(soffice),
        "--headless",
        "--nologo",
        "--norestore",
        f"-env:UserInstallation={profile_url}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(ppt_path.parent),
        str(ppt_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("ppt_preview_soffice_failed file=%s error=%s", ppt_path.name, exc)
        return None
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

    if result.returncode != 0 or not pdf_path.is_file():
        logger.warning(
            "ppt_preview_soffice_failed file=%s rc=%s stderr=%s",
            ppt_path.name,
            result.returncode,
            (result.stderr or "").strip()[:500],
        )
        return None

    logger.info(
        "ppt_preview_converted engine=libreoffice file=%s pdf=%s bytes=%s",
        ppt_path.name,
        pdf_path.name,
        pdf_path.stat().st_size,
    )
    return pdf_path


def _convert_with_powerpoint(ppt_path: Path, pdf_path: Path) -> Path | None:
    """Convert through local Microsoft PowerPoint (COM). Windows only."""
    if not sys.platform.startswith("win"):
        logger.warning(
            "ppt_preview_skipped reason=no_render_engine file=%s", ppt_path.name
        )
        return None

    try:
        import pythoncom  # type: ignore[import-not-found]
        import win32com.client  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "ppt_preview_skipped reason=no_render_engine file=%s "
            "hint=install pywin32 or LibreOffice",
            ppt_path.name,
        )
        return None

    app = None
    presentation = None
    pythoncom.CoInitialize()
    try:
        app = win32com.client.Dispatch("PowerPoint.Application")
        # Open(FileName, ReadOnly, Untitled, WithWindow)
        presentation = app.Presentations.Open(str(ppt_path.resolve()), True, False, False)
        presentation.SaveAs(str(pdf_path.resolve()), _PP_SAVE_AS_PDF)
    except Exception as exc:  # noqa: BLE001 - COM raises many different errors
        logger.warning(
            "ppt_preview_powerpoint_failed file=%s error=%s", ppt_path.name, exc
        )
        return None
    finally:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:  # noqa: BLE001
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:  # noqa: BLE001
                pass
        pythoncom.CoUninitialize()

    if not pdf_path.is_file():
        logger.warning(
            "ppt_preview_powerpoint_failed file=%s error=pdf_missing", ppt_path.name
        )
        return None

    logger.info(
        "ppt_preview_converted engine=powerpoint file=%s pdf=%s bytes=%s",
        ppt_path.name,
        pdf_path.name,
        pdf_path.stat().st_size,
    )
    return pdf_path
