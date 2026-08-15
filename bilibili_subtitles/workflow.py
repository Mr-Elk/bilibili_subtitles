from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import msvcrt
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .core import PartMetadata, parse_bv_url, parse_srt, render_markdown, subtitle_choices


class NoSubtitlesError(RuntimeError):
    """Raised when none of the requested video parts has usable captions."""


class UnsupportedVideoStructureError(RuntimeError):
    """Raised when yt-dlp returns entries that are not ordinary video parts."""


class ConcurrentExtractionError(RuntimeError):
    """Raised when another process is extracting the same video."""


@dataclass(frozen=True)
class ExtractionResult:
    success_count: int
    no_subtitle_count: int
    output_dir: Path


def _is_anthology_entry(entry: dict, bvid: str) -> bool:
    match = re.fullmatch(rf"{re.escape(bvid)}_p([1-9]\d*)", entry.get("id") or "")
    if match is None:
        return False
    webpage_url = urlparse(entry.get("webpage_url") or "")
    return (
        webpage_url.scheme == "https"
        and webpage_url.hostname in {"bilibili.com", "www.bilibili.com"}
        and webpage_url.path.rstrip("/") == f"/video/{bvid}"
        and parse_qs(webpage_url.query).get("p") == [match.group(1)]
    )


def _collapse_multi_video(info: dict) -> dict:
    fragments = list(info.get("entries") or [])
    if not fragments:
        raise UnsupportedVideoStructureError("Legacy video has no media fragments.")
    logical_video = dict(fragments[0])
    for field in ("id", "title", "uploader", "webpage_url"):
        if info.get(field) is not None:
            logical_video[field] = info[field]
    logical_video["_type"] = "video"
    logical_video["subtitles"] = fragments[0].get("subtitles") or {}
    return logical_video


def _logical_entries(info: dict, bvid: str) -> list[dict]:
    result_type = info.get("_type", "video")
    if result_type == "video":
        return [info]
    if result_type == "multi_video":
        return [_collapse_multi_video(info)]
    if result_type == "playlist":
        entries = list(info.get("entries") or [])
        if entries and all(_is_anthology_entry(entry, bvid) for entry in entries):
            return [
                _collapse_multi_video(entry)
                if entry.get("_type", "video") == "multi_video"
                else entry
                for entry in entries
            ]
    raise UnsupportedVideoStructureError(
        "Interactive or unknown Bilibili video structures are not supported."
    )


def _recover_output(output_root: Path, output_dir: Path, bvid: str) -> None:
    staging_dirs = [
        path
        for path in output_root.glob(f".{bvid}-*")
        if path.is_dir() and "-backup-" not in path.name
    ]
    for staging_dir in staging_dirs:
        shutil.rmtree(staging_dir, ignore_errors=True)
    backups = sorted(
        (
            path
            for path in output_root.glob(f".{bvid}-backup-*")
            if path.is_dir()
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if output_dir.exists():
        for backup in backups:
            shutil.rmtree(backup, ignore_errors=True)
        return
    if not backups:
        return
    backups[0].rename(output_dir)
    for backup in backups[1:]:
        shutil.rmtree(backup, ignore_errors=True)


@contextmanager
def _video_lock(output_root: Path, bvid: str):
    lock_path = output_root / f".{bvid}.lock"
    lock_file = lock_path.open("a+b")
    try:
        lock_file.seek(0, 2)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise ConcurrentExtractionError(
                f"Another extraction is already running for {bvid}."
            ) from error
        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        lock_file.close()


def extract_to_markdown(
    raw_url: str,
    *,
    output_root: Path,
    fetch_info: Callable[[str], dict],
    extracted_at: str,
) -> ExtractionResult:
    parsed_url = parse_bv_url(raw_url)
    output_root.mkdir(parents=True, exist_ok=True)
    with _video_lock(output_root, parsed_url.bvid):
        return _extract_to_markdown_locked(
            parsed_url,
            output_root=output_root,
            fetch_info=fetch_info,
            extracted_at=extracted_at,
        )


def _extract_to_markdown_locked(
    parsed_url,
    *,
    output_root: Path,
    fetch_info: Callable[[str], dict],
    extracted_at: str,
) -> ExtractionResult:
    output_dir = output_root / parsed_url.bvid
    _recover_output(output_root, output_dir, parsed_url.bvid)
    info = fetch_info(parsed_url.canonical_url)
    entries = _logical_entries(info, parsed_url.bvid)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{parsed_url.bvid}-", dir=output_root)
    )

    try:
        success_count = 0
        no_subtitle_count = 0
        index_lines = [f"# {info.get('title') or parsed_url.bvid}", ""]
        for part_number, entry in enumerate(entries, start=1):
            title = entry.get("title") or f"Part {part_number}"
            selected = None
            captions = []
            parse_error = None
            for candidate in subtitle_choices(entry.get("subtitles") or {}):
                try:
                    candidate_captions = parse_srt(candidate.srt)
                except ValueError as error:
                    parse_error = error
                    continue
                if candidate_captions:
                    selected = candidate
                    captions = candidate_captions
                    break
            if selected is None and parse_error is not None:
                raise ValueError(f"No usable subtitle track for {title}.") from parse_error
            if selected is None:
                no_subtitle_count += 1
                index_lines.append(f"- {title}: `no_subtitles`")
                continue

            filename = f"part-{part_number:03d}.md"
            source_url = entry.get("webpage_url") or (
                f"{parsed_url.canonical_url}?p={part_number}"
            )
            metadata = PartMetadata(
                bvid=parsed_url.bvid,
                part_number=part_number,
                title=title,
                uploader=entry.get("uploader") or info.get("uploader") or "Unknown",
                source_url=source_url,
                language=selected.language,
                extracted_at=extracted_at,
            )
            (staging_dir / filename).write_text(
                render_markdown(metadata, captions),
                encoding="utf-8",
            )
            success_count += 1
            index_lines.append(f"- [{title}]({filename}) (`{selected.language}`)")

        if success_count == 0:
            raise NoSubtitlesError(
                f"No existing captions were found for {parsed_url.bvid}."
            )

        (staging_dir / "index.md").write_text(
            "\n".join(index_lines) + "\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    backup_dir = output_root / f".{parsed_url.bvid}-backup-{uuid4().hex}"
    try:
        if output_dir.exists():
            output_dir.rename(backup_dir)
        staging_dir.rename(output_dir)
    except Exception:
        if backup_dir.exists() and not output_dir.exists():
            backup_dir.rename(output_dir)
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    shutil.rmtree(backup_dir, ignore_errors=True)

    return ExtractionResult(
        success_count=success_count,
        no_subtitle_count=no_subtitle_count,
        output_dir=output_dir,
    )
