import json
from pathlib import Path
import re


SCHEMA_VERSION = 1
_PART_FILE = re.compile(r"part-(\d{3,})\.md")


def new_manifest(
    *,
    bvid: str,
    title: str,
    source_url: str,
    updated_at: str,
    coverage_complete: bool,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "bvid": bvid,
        "title": title,
        "source_url": source_url,
        "updated_at": updated_at,
        "coverage_complete": coverage_complete,
        "last_request": {"mode": "unknown"},
        "parts": [],
    }


def _valid_part(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    part_number = item.get("part_number")
    if isinstance(part_number, bool) or not isinstance(part_number, int):
        return False
    if part_number < 1 or item.get("status") not in {"captioned", "no_subtitles"}:
        return False
    if not isinstance(item.get("title"), str) or not isinstance(
        item.get("source_url"), str
    ):
        return False
    if item["status"] == "captioned":
        return (
            item.get("file") == f"part-{part_number:03d}.md"
            and isinstance(item.get("language"), str)
            and item.get("extraction_method") == "existing_bilibili_captions"
        )
    return item.get("file") is None and item.get("language") is None


def _read_existing_manifest(output_dir: Path, bvid: str) -> dict | None:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("bvid") != bvid
        or not isinstance(manifest.get("title"), str)
        or not isinstance(manifest.get("source_url"), str)
        or not isinstance(manifest.get("updated_at"), str)
        or not isinstance(manifest.get("parts"), list)
        or not all(_valid_part(item) for item in manifest["parts"])
    ):
        return None
    part_numbers = [item["part_number"] for item in manifest["parts"]]
    if len(part_numbers) != len(set(part_numbers)):
        return None
    for item in manifest["parts"]:
        if item["status"] == "captioned" and not (
            output_dir / item["file"]
        ).is_file():
            return None
    manifest["parts"] = sorted(
        manifest["parts"], key=lambda item: item["part_number"]
    )
    manifest["coverage_complete"] = bool(manifest.get("coverage_complete"))
    return manifest


def _markdown_metadata(path: Path, bvid: str) -> dict | None:
    match = _PART_FILE.fullmatch(path.name)
    if match is None or int(match.group(1)) < 1:
        return None
    values = {}
    try:
        with path.open(encoding="utf-8") as transcript:
            for line_number, raw_line in enumerate(transcript):
                line = raw_line.rstrip("\r\n")
                if line.startswith("# ") and "title" not in values:
                    values["title"] = line[2:].strip()
                elif line.startswith("- BV: `"):
                    values["bvid"] = line.removeprefix("- BV: `").removesuffix("`")
                elif line.startswith("- Source: "):
                    values["source_url"] = line.removeprefix("- Source: ").strip()
                elif line.startswith("- Subtitle language: `"):
                    values["language"] = line.removeprefix(
                        "- Subtitle language: `"
                    ).removesuffix("`")
                if line == "## Transcript" or line_number >= 40:
                    break
    except (OSError, UnicodeError):
        return None
    if values.get("bvid") != bvid or not all(
        values.get(name) for name in ("title", "source_url", "language")
    ):
        return None
    part_number = int(match.group(1))
    return {
        "part_number": part_number,
        "title": values["title"],
        "status": "captioned",
        "source_url": values["source_url"],
        "language": values["language"],
        "file": path.name,
        "extraction_method": "existing_bilibili_captions",
    }


def load_or_migrate_manifest(
    output_dir: Path,
    *,
    bvid: str,
    fallback_title: str,
    source_url: str,
    updated_at: str,
) -> dict:
    existing = _read_existing_manifest(output_dir, bvid)
    if existing is not None:
        return existing

    title = fallback_title
    index_path = output_dir / "index.md"
    if index_path.is_file():
        try:
            with index_path.open(encoding="utf-8") as index_file:
                first_line = index_file.readline().strip()
            if first_line.startswith("# "):
                title = first_line[2:].strip() or fallback_title
        except (OSError, UnicodeError):
            pass
    manifest = new_manifest(
        bvid=bvid,
        title=title,
        source_url=source_url,
        updated_at=updated_at,
        coverage_complete=False,
    )
    if output_dir.is_dir():
        manifest["parts"] = [
            item
            for path in sorted(output_dir.glob("part-*.md"))
            if (item := _markdown_metadata(path, bvid)) is not None
        ]
    return manifest


def upsert_part(manifest: dict, part: dict) -> None:
    if not _valid_part(part):
        raise ValueError("Cannot write an invalid subtitle manifest part.")
    parts = {
        item["part_number"]: item
        for item in manifest.get("parts", [])
        if _valid_part(item)
    }
    parts[part["part_number"]] = part
    manifest["parts"] = [parts[number] for number in sorted(parts)]


def render_index(manifest: dict) -> str:
    lines = [f"# {manifest['title']}", ""]
    for part in manifest["parts"]:
        if part["status"] == "captioned":
            lines.append(
                f"- [{part['title']}]({part['file']}) (`{part['language']}`)"
            )
        else:
            lines.append(f"- {part['title']}: `no_subtitles`")
    return "\n".join(lines) + "\n"


def write_manifest(output_dir: Path, manifest: dict) -> None:
    if not all(_valid_part(item) for item in manifest.get("parts", [])):
        raise ValueError("Cannot write an invalid subtitle manifest.")
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
