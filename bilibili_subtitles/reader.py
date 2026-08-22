from dataclasses import dataclass
import json
from pathlib import Path
import re

from .output_manifest import ManifestValidationError, read_manifest


class ReaderInputError(ValueError):
    """Raised when a local transcript read request is invalid."""


@dataclass(frozen=True)
class LocalCaption:
    source: Path
    timestamp: str
    seconds: int
    text: str


_TIMESTAMP = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})$")
_CAPTION_LINE = re.compile(r"^\[(\d{2,}:\d{2}:\d{2})\]\s+(\S.*)$")
_DEFAULT_MAX_CHARS = {
    "status": 6_000,
    "inventory": 6_000,
    "map": 8_000,
    "search": 4_000,
    "slice": 6_000,
}
_OUTPUT_FORMATS = {"text", "json"}
_READER_SCHEMA = "bilibili-subtitles.reader"
_READER_SCHEMA_VERSION = 1


def timestamp_to_seconds(timestamp: str) -> int:
    match = _TIMESTAMP.fullmatch(timestamp)
    if match is None:
        raise ReaderInputError(f"Invalid timestamp: {timestamp}")
    hours, minutes, seconds = (int(value) for value in match.groups())
    if minutes > 59 or seconds > 59:
        raise ReaderInputError(f"Invalid timestamp: {timestamp}")
    return hours * 3_600 + minutes * 60 + seconds


def transcript_files(target: str | Path) -> list[Path]:
    path = Path(target)
    if not path.exists():
        raise ReaderInputError(f"Path does not exist: {path}")
    if path.is_file():
        return [path]
    files = sorted(
        (item for item in path.glob("part-*.md") if item.is_file()),
        key=lambda item: item.name,
    )
    if not files:
        raise ReaderInputError(f"No part-*.md transcripts found in: {path}")
    return files


def captions_from_file(path: Path) -> list[LocalCaption]:
    captions = []
    try:
        with path.open(encoding="utf-8") as transcript:
            for raw_line in transcript:
                match = _CAPTION_LINE.fullmatch(raw_line.rstrip("\r\n"))
                if match is None:
                    continue
                timestamp, text = match.groups()
                captions.append(
                    LocalCaption(
                        source=path,
                        timestamp=timestamp,
                        seconds=timestamp_to_seconds(timestamp),
                        text=text.strip(),
                    )
                )
    except (OSError, UnicodeError) as error:
        raise ReaderInputError(f"Cannot read transcript: {path}") from error
    if not captions:
        raise ReaderInputError(f"No timestamped captions found in: {path}")
    return captions


def transcript_title(path: Path) -> str:
    try:
        with path.open(encoding="utf-8") as transcript:
            for raw_line in transcript:
                if raw_line.startswith("# "):
                    title = re.sub(r"\s+", " ", raw_line[2:].strip())
                    return title[:120]
    except (OSError, UnicodeError) as error:
        raise ReaderInputError(f"Cannot read transcript: {path}") from error
    return ""


def status_manifest(target: str | Path) -> dict:
    output_dir = Path(target)
    if not output_dir.exists():
        raise ReaderInputError(f"Path does not exist: {output_dir}")
    if not output_dir.is_dir():
        raise ReaderInputError("Status requires a BV output directory.")
    try:
        return read_manifest(output_dir)
    except ManifestValidationError as error:
        raise ReaderInputError(str(error)) from error


def status_summary(manifest: dict) -> dict[str, object]:
    captioned_count = sum(
        part["status"] == "captioned" for part in manifest["parts"]
    )
    no_subtitles_count = sum(
        part["status"] == "no_subtitles" for part in manifest["parts"]
    )
    return {
        "schema_version": manifest["schema_version"],
        "bvid": manifest["bvid"],
        "title": manifest["title"],
        "source_url": manifest["source_url"],
        "updated_at": manifest["updated_at"],
        "coverage_complete": manifest["coverage_complete"],
        "last_request": dict(manifest["last_request"]),
        "part_count": len(manifest["parts"]),
        "captioned_count": captioned_count,
        "no_subtitles_count": no_subtitles_count,
    }


def status_items(manifest: dict) -> list[dict[str, object]]:
    return [
        {
            "part_number": part["part_number"],
            "title": part["title"],
            "status": part["status"],
            "source_url": part["source_url"],
            "language": part["language"],
            "file": part["file"],
            "extraction_method": part["extraction_method"],
        }
        for part in manifest["parts"]
    ]


def status(target: str | Path) -> list[str]:
    manifest = status_manifest(target)
    summary = status_summary(manifest)
    request = json.dumps(
        summary["last_request"], ensure_ascii=False, separators=(",", ":")
    )
    lines = [
        f"BV\t{summary['bvid']}",
        f"TITLE\t{summary['title']}",
        f"UPDATED\t{summary['updated_at']}",
        f"COVERAGE\t{'complete' if summary['coverage_complete'] else 'incomplete'}",
        f"LAST_REQUEST\t{request}",
        (
            f"PARTS\t{summary['part_count']}\tCAPTIONED\t"
            f"{summary['captioned_count']}\tNO_SUBTITLES\t"
            f"{summary['no_subtitles_count']}"
        ),
        "PART\tSTATUS\tLANGUAGE\tFILE\tTITLE",
    ]
    for item in status_items(manifest):
        language = item["language"] or "-"
        filename = item["file"] or "-"
        title = re.sub(r"\s+", " ", str(item["title"]).strip())
        lines.append(
            f"{item['part_number']}\t{item['status']}\t{language}\t{filename}\t{title}"
        )
    return lines


def inventory_items(files: list[Path]) -> list[dict[str, object]]:
    items = []
    for path in files:
        captions = captions_from_file(path)
        items.append(
            {
                "file": path.name,
                "cue_count": len(captions),
                "start": captions[0].timestamp,
                "end": captions[-1].timestamp,
                "text_chars": sum(len(caption.text) for caption in captions),
                "title": transcript_title(path),
            }
        )
    return items


def inventory(files: list[Path]) -> list[str]:
    lines = ["FILE\tCUES\tRANGE\tTEXT_CHARS\tTITLE"]
    for item in inventory_items(files):
        lines.append(
            f"{item['file']}\t{item['cue_count']}\t"
            f"{item['start']}-{item['end']}\t"
            f"{item['text_chars']}\t{item['title']}"
        )
    return lines


def chunk_items(files: list[Path], *, chunk_chars: int) -> list[dict[str, object]]:
    if chunk_chars < 200:
        raise ReaderInputError("ChunkChars must be at least 200.")
    items = []
    for path in files:
        captions = captions_from_file(path)
        chunk: list[LocalCaption] = []
        current_chars = 0
        chunk_number = 0

        def append_chunk() -> None:
            nonlocal chunk_number
            chunk_number += 1
            items.append(
                {
                    "chunk_id": f"{path.name}#{chunk_number:03d}",
                    "file": path.name,
                    "chunk": chunk_number,
                    "start": chunk[0].timestamp,
                    "end": chunk[-1].timestamp,
                    "cue_count": len(chunk),
                    "text_chars": current_chars,
                }
            )

        for caption in captions:
            caption_chars = len(caption.text) + 1
            if chunk and current_chars + caption_chars > chunk_chars:
                append_chunk()
                chunk = []
                current_chars = 0
            chunk.append(caption)
            current_chars += caption_chars
        if chunk:
            append_chunk()
    return items


def chunk_map(files: list[Path], *, chunk_chars: int) -> list[str]:
    lines = ["CHUNK\tRANGE\tCUES\tTEXT_CHARS"]
    for item in chunk_items(files, chunk_chars=chunk_chars):
        lines.append(
            f"{item['chunk_id']}\t{item['start']}-{item['end']}\t"
            f"{item['cue_count']}\t{item['text_chars']}"
        )
    return lines


def _caption_item(
    caption: LocalCaption,
    *,
    is_match: bool | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "file": caption.source.name,
        "timestamp": caption.timestamp,
        "seconds": caption.seconds,
        "text": caption.text,
    }
    if is_match is not None:
        item["is_match"] = is_match
    return item


def _format_caption_item(item: dict[str, object], *, include_source: bool) -> str:
    if include_source:
        return f"[{item['file']} {item['timestamp']}] {item['text']}"
    return f"[{item['timestamp']}] {item['text']}"


def search_items(
    files: list[Path],
    *,
    query: str,
    context: int,
    max_results: int,
) -> tuple[list[dict[str, object]], int]:
    if not query:
        raise ReaderInputError("Search requires a non-empty query.")
    if context < 0 or context > 50:
        raise ReaderInputError("Context must be from 0 to 50.")
    if max_results < 1 or max_results > 1_000:
        raise ReaderInputError("MaxResults must be from 1 to 1000.")
    items = []
    matched_count = 0
    folded_query = query.casefold()
    for path in files:
        if matched_count >= max_results:
            break
        captions = captions_from_file(path)
        selected_indices: dict[int, bool] = {}
        for index, caption in enumerate(captions):
            if matched_count >= max_results:
                break
            if folded_query not in caption.text.casefold():
                continue
            matched_count += 1
            first_index = max(0, index - context)
            last_index = min(len(captions) - 1, index + context)
            for selected_index in range(first_index, last_index + 1):
                selected_indices.setdefault(selected_index, False)
            selected_indices[index] = True
        items.extend(
            _caption_item(captions[index], is_match=is_match)
            for index, is_match in sorted(selected_indices.items())
        )
    return items, matched_count


def search(
    files: list[Path],
    *,
    query: str,
    context: int,
    max_results: int,
) -> list[str]:
    items, _ = search_items(
        files,
        query=query,
        context=context,
        max_results=max_results,
    )
    return [
        _format_caption_item(item, include_source=True) for item in items
    ] or ["NO_MATCH"]


def slice_items(
    files: list[Path],
    *,
    start: str,
    end: str | None,
) -> list[dict[str, object]]:
    start_seconds = timestamp_to_seconds(start)
    end_seconds = timestamp_to_seconds(end) if end else None
    if end_seconds is not None and end_seconds < start_seconds:
        raise ReaderInputError("End must not be earlier than Start.")
    items = []
    for path in files:
        for caption in captions_from_file(path):
            if caption.seconds < start_seconds:
                continue
            if end_seconds is not None and caption.seconds > end_seconds:
                continue
            items.append(_caption_item(caption))
    return items


def slice_captions(
    files: list[Path],
    *,
    start: str,
    end: str | None,
) -> list[str]:
    items = slice_items(files, start=start, end=end)
    include_source = len(files) > 1
    return [
        _format_caption_item(item, include_source=include_source) for item in items
    ] or ["NO_CAPTIONS_IN_RANGE"]


def bounded_text(lines: list[str], *, limit: int) -> str:
    if limit < 200:
        raise ReaderInputError("MaxChars must be at least 200.")
    rendered = "\n".join(str(line) for line in lines).rstrip()
    if len(rendered) > limit:
        marker = "\n... [truncated; narrow the query/range or raise --max-chars]"
        prefix_length = max(0, limit - len(marker))
        rendered = rendered[:prefix_length] + marker
    return rendered


def reader_payload(
    action: str,
    *,
    target: str | Path,
    query: str | None = None,
    start: str = "00:00:00",
    end: str | None = None,
    context: int = 1,
    max_results: int = 8,
    chunk_chars: int = 5_000,
) -> dict[str, object]:
    normalized_action = action.lower()
    if normalized_action not in _DEFAULT_MAX_CHARS:
        raise ReaderInputError(f"Unsupported local read action: {action}")
    match_count = None
    manifest_summary = None
    if normalized_action == "status":
        parameters: dict[str, object] = {}
        manifest = status_manifest(target)
        manifest_summary = status_summary(manifest)
        items = status_items(manifest)
    else:
        files = transcript_files(target)
        if normalized_action == "inventory":
            parameters = {}
            items = inventory_items(files)
        elif normalized_action == "map":
            parameters = {"chunk_chars": chunk_chars}
            items = chunk_items(files, chunk_chars=chunk_chars)
        elif normalized_action == "search":
            effective_query = query or ""
            parameters = {
                "query": effective_query,
                "context": context,
                "max_results": max_results,
            }
            items, match_count = search_items(
                files,
                query=effective_query,
                context=context,
                max_results=max_results,
            )
        else:
            parameters = {"start": start, "end": end}
            items = slice_items(files, start=start, end=end)

    payload: dict[str, object] = {
        "schema": _READER_SCHEMA,
        "schema_version": _READER_SCHEMA_VERSION,
        "action": normalized_action,
        "target": str(Path(target).resolve()),
        "parameters": parameters,
    }
    if match_count is not None:
        payload["match_count"] = match_count
    if manifest_summary is not None:
        payload["manifest"] = manifest_summary
    payload["items"] = items
    return payload


def bounded_json(payload: dict[str, object], *, limit: int) -> str:
    if limit < 200:
        raise ReaderInputError("MaxChars must be at least 200.")
    items = list(payload.get("items", []))
    base = {key: value for key, value in payload.items() if key != "items"}
    total_items = len(items)

    def render(returned_items: int) -> str:
        candidate = dict(base)
        candidate["item_count"] = total_items
        candidate["returned_count"] = returned_items
        candidate["truncated"] = returned_items < total_items
        candidate["items"] = items[:returned_items]
        return json.dumps(
            candidate,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    full = render(total_items)
    if len(full) <= limit:
        return full

    empty = render(0)
    if len(empty) > limit:
        raise ReaderInputError(
            "MaxChars is too small for JSON metadata; increase --max-chars."
        )

    best = empty
    low = 1
    high = total_items - 1
    while low <= high:
        middle = (low + high) // 2
        candidate = render(middle)
        if len(candidate) <= limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def read_transcripts(
    action: str,
    *,
    target: str | Path,
    query: str | None = None,
    start: str = "00:00:00",
    end: str | None = None,
    context: int = 1,
    max_results: int = 8,
    chunk_chars: int = 5_000,
    max_chars: int = 0,
    output_format: str = "text",
) -> str:
    normalized_action = action.lower()
    normalized_format = output_format.lower()
    if normalized_action not in _DEFAULT_MAX_CHARS:
        raise ReaderInputError(f"Unsupported local read action: {action}")
    if normalized_format not in _OUTPUT_FORMATS:
        raise ReaderInputError(f"Unsupported output format: {output_format}")
    if max_chars != 0 and max_chars < 200:
        raise ReaderInputError(
            "MaxChars must be 0 (use the action default) or at least 200."
        )
    output_limit = max_chars or _DEFAULT_MAX_CHARS[normalized_action]
    if normalized_format == "json":
        payload = reader_payload(
            normalized_action,
            target=target,
            query=query,
            start=start,
            end=end,
            context=context,
            max_results=max_results,
            chunk_chars=chunk_chars,
        )
        return bounded_json(payload, limit=output_limit)

    if normalized_action == "status":
        lines = status(target)
    else:
        files = transcript_files(target)
        if normalized_action == "inventory":
            lines = inventory(files)
        elif normalized_action == "map":
            lines = chunk_map(files, chunk_chars=chunk_chars)
        elif normalized_action == "search":
            lines = search(
                files,
                query=query or "",
                context=context,
                max_results=max_results,
            )
        else:
            lines = slice_captions(files, start=start, end=end)
    return bounded_text(lines, limit=output_limit)
