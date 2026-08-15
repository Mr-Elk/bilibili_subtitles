from dataclasses import dataclass
import re
from urllib.parse import urlparse


class InvalidBilibiliUrl(ValueError):
    """Raised when the input is not a supported Bilibili video URL."""


@dataclass(frozen=True)
class BilibiliUrl:
    bvid: str
    canonical_url: str


@dataclass(frozen=True)
class SubtitleChoice:
    language: str
    srt: str


@dataclass(frozen=True)
class Caption:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class PartMetadata:
    bvid: str
    part_number: int
    title: str
    uploader: str
    source_url: str
    language: str
    extracted_at: str


def parse_bv_url(raw_url: str) -> BilibiliUrl:
    parsed = urlparse(raw_url.strip())
    path_match = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10})/?", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() not in {"bilibili.com", "www.bilibili.com"}
        or path_match is None
    ):
        raise InvalidBilibiliUrl(
            "Only direct HTTPS bilibili.com/video/BV... URLs are supported."
        )

    bvid = path_match.group(1)
    return BilibiliUrl(
        bvid=bvid,
        canonical_url=f"https://www.bilibili.com/video/{bvid}",
    )


def subtitle_choices(
    subtitles: dict[str, list[dict[str, str]]],
) -> list[SubtitleChoice]:
    languages = []
    if subtitles.get("zh-Hans"):
        languages.append("zh-Hans")
    languages.extend(
        name
        for name, variants in subtitles.items()
        if name not in languages
        and name.lower().startswith(("zh", "ai-zh"))
        and variants
    )
    languages.extend(
        name
        for name, variants in subtitles.items()
        if name not in languages and name.lower() != "danmaku" and variants
    )

    choices = []
    for language in languages:
        for variant in subtitles[language]:
            srt = variant.get("data")
            if isinstance(srt, str) and srt.strip():
                choices.append(SubtitleChoice(language=language, srt=srt))
    return choices


def choose_subtitle(subtitles: dict[str, list[dict[str, str]]]) -> SubtitleChoice | None:
    choices = subtitle_choices(subtitles)
    return choices[0] if choices else None


def _timestamp_ms(value: str) -> int:
    hours, minutes, seconds_and_ms = value.replace(".", ",").split(":")
    seconds, milliseconds = seconds_and_ms.split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(milliseconds)
    )


def parse_srt(srt: str) -> list[Caption]:
    normalized = srt.replace("\r\n", "\n").replace("\r", "\n").strip()
    captions: list[Caption] = []
    for block in re.split(r"\n{2,}", normalized):
        lines = block.splitlines()
        timing_index = next(
            (index for index, line in enumerate(lines) if " --> " in line),
            None,
        )
        if timing_index is None:
            continue
        start, end = lines[timing_index].split(" --> ", 1)
        text = " ".join(line.strip() for line in lines[timing_index + 1 :] if line.strip())
        if not text:
            continue
        captions.append(
            Caption(
                start_ms=_timestamp_ms(start.strip()),
                end_ms=_timestamp_ms(end.strip()),
                text=re.sub(r"\s+", " ", text),
            )
        )
    return captions


def _display_timestamp(milliseconds: int) -> str:
    total_seconds = milliseconds // 1_000
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def render_markdown(metadata: PartMetadata, captions: list[Caption]) -> str:
    caption_lines = "\n".join(
        f"[{_display_timestamp(caption.start_ms)}] {caption.text}"
        for caption in captions
    )
    return (
        f"# {metadata.title}\n\n"
        f"- BV: `{metadata.bvid}`\n"
        f"- Part: `{metadata.part_number}`\n"
        f"- Uploader: {metadata.uploader}\n"
        f"- Source: {metadata.source_url}\n"
        f"- Subtitle language: `{metadata.language}`\n"
        f"- Extracted at: `{metadata.extracted_at}`\n"
        "- Extraction method: existing Bilibili captions (no speech-to-text)\n\n"
        "> Local study copy. Verify quotations against the source video and its terms.\n\n"
        "## Transcript\n\n"
        f"{caption_lines}\n"
    )
