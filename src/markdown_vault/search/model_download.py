"""UI-agnostic model downloader — the network fetch and the FS writes for a single-file
model (ONNX / GGUF) from an arbitrary HTTPS URL.

The download owns everything below the widget: the HTTPS check, the redirect guard, the
streaming ``.part``-then-rename, and content validation. The UI keeps only the button,
the progress bar and the toast, and maps a failure through :func:`describe_error` so a
foreign ``str(exc)`` never reaches a translated message.

Multi-file model *sets* (Whisper) are a different job — they come from a Hugging Face
repo via ``huggingface_hub.snapshot_download`` (see ``importers.document_import``), not a
single URL — and stay there; only the error mapping is shared in spirit.
"""

import logging
import urllib.error
import urllib.request
from urllib.parse import urlparse

from markdown_vault.core import state_fs
from markdown_vault.core.i18n import _

logger = logging.getLogger(__name__)

_CHUNK = 65536
_PROGRESS_STEP = 1024 * 1024   # report progress roughly every megabyte


class ModelDownloadError(Exception):
    """Base for a failure we classify ourselves; a foreign urllib/OS exception is mapped
    by type in :func:`describe_error` instead."""


class NonHttpsUrl(ModelDownloadError):
    """The download URL is not HTTPS — refused before any network access."""


class ContentRejected(ModelDownloadError):
    """The bytes failed the caller's ``validate()`` (e.g. an HTML page where a GGUF was
    expected). Carries the already-translated reason as its message."""


class IncompleteDownload(ModelDownloadError):
    """Fewer bytes arrived than the server's Content-Length promised. ``http.client``
    returns an empty read (no exception) when a Content-Length response drops, so without
    this check a truncated model would be renamed into place and reported complete — and
    handed to a native GGUF/ONNX parser, a memory-safety surface, not a parse error."""


class InsecureRedirect(urllib.error.HTTPError):
    """A redirect tried to leave HTTPS. Subclasses ``HTTPError`` so urllib's redirect
    machinery propagates it unchanged. A downgraded, unauthenticated file would reach a
    native parser (llama.cpp's GGUF loader, ONNX Runtime's protobuf) — a memory-safety
    surface, not a mere parse error."""


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise InsecureRedirect(newurl, code, "refusing a non-HTTPS redirect",
                                   headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_to(url, target, *, validate=None, progress=None) -> int:
    """Download *url* into *target* (a ``Path``), streaming through a ``.part`` file and
    renaming on success so a half-written file never looks complete. A response short of
    its Content-Length is rejected (:class:`IncompleteDownload`) and the ``.part`` is
    removed on any failure, so the caller never has to clean up a partial itself.

    Refuses a non-HTTPS URL or an off-HTTPS redirect. If *validate* is given it is called
    with the temp path; a truthy return rejects the download (the value is the reason,
    raised as :class:`ContentRejected`). *progress(done, total)* is called about every
    megabyte. Returns the byte size. Raises :class:`ModelDownloadError` or a urllib error.
    """
    if urlparse(url).scheme != "https":
        raise NonHttpsUrl(url)
    req = urllib.request.Request(url, headers={"User-Agent": "markdown-vault"})
    opener = urllib.request.build_opener(_HttpsOnlyRedirect())
    with opener.open(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length") or 0)

        def _stream():
            """Yield the response body, reporting progress and refusing a short read.

            Raising from HERE rather than after the write is what disarms a truncated
            download: state_fs.write_stream tears down its ``.part`` on any exception from
            the producer, so the partial never reaches the rename.

            The guarantee is exactly as wide as the header: a transfer whose length the
            server ANNOUNCED cannot land truncated. Without a ``Content-Length`` there is
            nothing to compare against and an interruption is undetectable here — *validate*
            does not close that gap either, since it tests the format at the start of the
            file and a truncated download is well-formed at the front.
            """
            done = last = 0
            while True:
                buf = resp.read(_CHUNK)
                if not buf:
                    break
                done += len(buf)
                if progress and done - last >= _PROGRESS_STEP:
                    last = done
                    progress(done, total)
                yield buf
            if total and done != total:    # a dropped connection ends the read with no error
                raise IncompleteDownload(f"got {done} of {total} bytes")

        try:
            # StateFS owns the .part dance, the parent directory, the validation gate and
            # the cleanup — and it is the only op whose containment admits the user-picked
            # model folders. This module keeps the network half.
            return state_fs.write_stream(str(target), _stream(), validate=validate)
        except state_fs.RejectedContent as exc:
            # Re-raised in this module's own vocabulary: describe_error passes
            # ContentRejected's text through as an already-translated reason, and the
            # facade's type would miss that branch and lose it to the catch-all.
            raise ContentRejected(str(exc)) from exc


def describe_error(exc: BaseException) -> str:
    """A translated, user-facing message for a download failure — never a raw ``str(exc)``.
    Most-specific first: ``InsecureRedirect ⊂ HTTPError ⊂ URLError``, so the order is the
    whole point (a foreign 3xx must land on the generic server branch, not the redirect
    one)."""
    if isinstance(exc, NonHttpsUrl):
        return _("Refusing a non-HTTPS download URL.")
    if isinstance(exc, IncompleteDownload):
        return _("The download was incomplete — the connection dropped. Try again.")
    if isinstance(exc, InsecureRedirect):
        return _("The download was redirected off HTTPS and refused.")
    if isinstance(exc, ContentRejected):
        return str(exc)                    # already our own translated reason
    if isinstance(exc, state_fs.StateWriteError):
        # The configured model folder is not a place we may write into (inside a vault, or
        # under no allowed root). Naming the folder is what the user can act on.
        return _("The configured model folder cannot be written to. Choose another one in "
                 "Preferences.")
    if isinstance(exc, urllib.error.HTTPError):
        return _("The server returned an error (HTTP {code}).").format(code=exc.code)
    if isinstance(exc, urllib.error.URLError):
        return _("Could not reach the download server.")
    return _("The download failed. See the log for details.")
