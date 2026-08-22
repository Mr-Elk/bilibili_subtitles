import json
from pathlib import Path
import re


SCHEMA_VERSION = 1
_PART_FILE = re.compile(r"part-(\d{3,})\.md")


class ManifestValidationError(ValueError):
    """Raised when an existing subtitle manifest cannot be trusted."""


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


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
    if not _nonempty_string(item.get("title")) or not _nonempty_string(
        item.get("source_url")
    ):
        return False
    if item["status"] == "captioned":
        return (
            item.get("file") == f"part-{part_number:03d}.md"
            and _nonempty_string(item.get("language"))
            and item.get("extraction_method") == "existing_bilibili_captions"
        )
    return (
        item.get("file") is None
        and item.get("language") is None
        and item.get("extraction_method") is None
    )


def _valid_last_request(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    mode = value.get("mode")
    if mode in {"all", "unknown"}:
        return True
    if mode != "part":
        return False
    part_number = value.get("part_number")
    return type(part_number) is int and part_number > 0


def _validate_manifest(
    manifest: object,
    *,
    output_dir: Path | None = None,
    expected_bvid: str | None = None,
) -> dict:
    if not isinstance(manifest, dict):
        raise ManifestValidationError("manifest.json must contain a JSON object.")
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise ManifestValidationError(
            f"Unsupported manifest.json schema_version: {schema_version!r}."
        )
    bvid = manifest.get("bvid")
    if not _nonempty_string(bvid) or (
        expected_bvid is not None and bvid != expected_bvid
    ):
        raise ManifestValidationError("manifest.json has an invalid BV identifier.")
    if not all(
        _nonempty_string(manifest.get(field))
        for field in ("title", "source_url", "updated_at")
    ):
        raise ManifestValidationError("manifest.json has invalid source metadata.")
    if type(manifest.get("coverage_complete")) is not bool:
        raise ManifestValidationError("manifest.json coverage_complete must be a boolean.")
    if not _valid_last_request(manifest.get("last_request")):
        raise ManifestValidationError("manifest.json has an invalid last_request.")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not all(_valid_part(item) for item in parts):
        raise ManifestValidationError("manifest.json contains an invalid part entry.")
    part_numbers = [item["part_number"] for item in parts]
    if len(part_numbers) != len(set(part_numbers)):
        raise ManifestValidationError("manifest.json contains duplicate part numbers.")

    if output_dir is not None:
        expected_files = {
            item["file"] for item in parts if item["status"] == "captioned"
        }
        actual_files = {
            path.name
            for path in output_dir.glob("part-*.md")
            if path.is_file()
        }
        missing_files = sorted(expected_files - actual_files)
        if missing_files:
            raise ManifestValidationError(
                "manifest.json references missing transcript files: "
                + ", ".join(missing_files)
            )
        orphan_files = sorted(actual_files - expected_files)
        if orphan_files:
            raise ManifestValidationError(
                "Transcript files are not represented by manifest.json: "
                + ", ".join(orphan_files)
            )

    validated = dict(manifest)
    validated["parts"] = sorted(
        (dict(item) for item in parts), key=lambda item: item["part_number"]
    )
    return validated


def read_manifest(
    output_dir: Path,
    *,
    expected_bvid: str | None = None,
) -> dict:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ManifestValidationError(f"No manifest.json found in: {output_dir}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestValidationError(
            f"Cannot read a valid UTF-8 manifest.json in: {output_dir}"
        ) from error
    return _validate_manifest(
        manifest,
        output_dir=output_dir,
        expected_bvid=expected_bvid,
    )


def _read_existing_manifest(output_dir: Path, bvid: str) -> dict | None:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    return read_manifest(output_dir, expected_bvid=bvid)


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
    validated = _validate_manifest(manifest, output_dir=output_dir)
    (output_dir / "manifest.json").write_text(
        json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
