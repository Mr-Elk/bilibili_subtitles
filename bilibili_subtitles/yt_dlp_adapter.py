import json
import re
import sys
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import parse_bv_url, parse_srt


_API_HOSTS = frozenset({"api.bilibili.com"})
_CAPTION_HOSTS = frozenset(
    {"aisubtitle.hdslb.com", "subtitle.bilibili.com"}
)
MAX_PUBLIC_JSON_BYTES = 8 * 1024 * 1024
MAX_PUBLIC_CAPTION_CUES = 100_000


class PublicCaptionLookupError(RuntimeError):
    """Raised when Bilibili's public caption data cannot be verified."""


def _validate_https_url(raw_url: str, allowed_hosts) -> str:
    parsed = urlparse(raw_url)
    try:
        port = parsed.port
    except ValueError as error:
        raise PublicCaptionLookupError("Public caption URL port is invalid.") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise PublicCaptionLookupError(
            "Public caption URL is not allowed by the HTTPS host boundary."
        )
    return parsed.geturl()


class _BoundaryRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validated_url = _validate_https_url(newurl, self.allowed_hosts)
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            validated_url,
        )


def _srt_timestamp(seconds: float) -> str:
    total_ms = round(float(seconds) * 1_000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def bilibili_json_to_srt(document: dict) -> str:
    if not isinstance(document, dict) or not isinstance(document.get("body"), list):
        raise ValueError("Public caption document has no body list.")
    if len(document["body"]) > MAX_PUBLIC_CAPTION_CUES:
        raise ValueError("Public caption document contains too many cues.")
    blocks = []
    for cue in document["body"]:
        if (
            not isinstance(cue, dict)
            or not isinstance(cue.get("from"), (int, float))
            or not isinstance(cue.get("to"), (int, float))
            or not isinstance(cue.get("content"), str)
        ):
            raise ValueError("Public caption document contains an invalid cue.")
        content = cue["content"].strip()
        if not content:
            continue
        blocks.append(
            f"{len(blocks) + 1}\n"
            f"{_srt_timestamp(cue['from'])} --> {_srt_timestamp(cue['to'])}\n"
            f"{content}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _download_public_json(url: str) -> dict:
    hostname = urlparse(url).hostname
    if hostname in _API_HOSTS:
        allowed_hosts = _API_HOSTS
    elif hostname in _CAPTION_HOSTS:
        allowed_hosts = _CAPTION_HOSTS
    else:
        raise PublicCaptionLookupError("Public caption URL host is not allowed.")
    validated_url = _validate_https_url(url, allowed_hosts)
    request = Request(
        validated_url,
        headers={
            "Referer": "https://www.bilibili.com/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    opener = build_opener(_BoundaryRedirectHandler(allowed_hosts))
    with opener.open(request, timeout=30) as response:
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_PUBLIC_JSON_BYTES:
                    raise PublicCaptionLookupError(
                        "Public caption response exceeds the size limit."
                    )
            except ValueError:
                pass
        payload = response.read(MAX_PUBLIC_JSON_BYTES + 1)
        if len(payload) > MAX_PUBLIC_JSON_BYTES:
            raise PublicCaptionLookupError(
                "Public caption response exceeds the size limit."
            )
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PublicCaptionLookupError(
                "Public caption response is not valid UTF-8 JSON."
            ) from error


def _api_data(document: dict, endpoint: str) -> dict:
    if not isinstance(document, dict):
        raise PublicCaptionLookupError(f"{endpoint} returned a non-object response.")
    if document.get("code") != 0:
        raise PublicCaptionLookupError(
            f"{endpoint} failed with code {document.get('code')}: "
            f"{document.get('message') or 'unknown error'}"
        )
    data = document.get("data")
    if not isinstance(data, dict):
        raise PublicCaptionLookupError(f"{endpoint} returned invalid data.")
    return data


def _is_chinese_language(language: str) -> bool:
    return language.lower().startswith(("zh", "ai-zh"))


def _has_usable_caption_data(info: dict, *, chinese_only=False) -> bool:
    for language, variants in (info.get("subtitles") or {}).items():
        if language.lower() == "danmaku" or (
            chinese_only and not _is_chinese_language(language)
        ):
            continue
        for variant in variants:
            data = variant.get("data")
            if not isinstance(data, str) or not data.strip():
                continue
            try:
                if parse_srt(data):
                    return True
            except ValueError:
                continue
    return False


def _has_chinese_caption_data(info: dict) -> bool:
    return _has_usable_caption_data(info, chinese_only=True)


def _ordered_public_tracks(tracks: list[dict]) -> list[dict]:
    def priority(track: dict) -> int:
        language = str(track.get("lan") or "").lower()
        if language == "zh-hans":
            return 0
        if language.startswith(("zh", "ai-zh")):
            return 1
        return 2

    available = []
    for track in tracks:
        if not isinstance(track.get("lan"), str) or not isinstance(
            track.get("subtitle_url"), str
        ):
            raise PublicCaptionLookupError(
                "Bilibili returned an invalid public subtitle track."
            )
        if track["subtitle_url"].strip():
            available.append(track)
    return sorted(available, key=priority)


def _caption_target(entry: dict) -> dict:
    if entry.get("_type") != "multi_video":
        return entry
    fragments = entry.get("entries") or []
    return fragments[0] if fragments and isinstance(fragments[0], dict) else entry


def _public_caption_entries(info: dict) -> list[tuple[dict, dict]]:
    if info.get("_type") == "multi_video":
        return [(info, _caption_target(info))]
    parents = list(info.get("entries") or [info])
    return [(parent, _caption_target(parent)) for parent in parents]


def _secure_subtitle_url(raw_url: str) -> str | None:
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"", "http", "https"} or parsed.hostname not in _CAPTION_HOSTS:
        return None
    try:
        return _validate_https_url(
            parsed._replace(scheme="https").geturl(),
            _CAPTION_HOSTS,
        )
    except PublicCaptionLookupError:
        return None


def _bounded_bilibili_extractor_class(base_class, fetch_json):
    class BoundedBilibiliIE(base_class):
        def _get_subtitles(self, video_id, cid, aid=None):
            subtitles = {
                "danmaku": [
                    {
                        "ext": "xml",
                        "url": f"https://comment.bilibili.com/{cid}.xml",
                    }
                ]
            }
            query = (
                {"aid": aid, "cid": cid}
                if aid
                else {"bvid": video_id, "cid": cid}
            )
            player_data = _api_data(
                self._download_json(
                    "https://api.bilibili.com/x/player/wbi/v2",
                    video_id,
                    query=query,
                    note=f"Extracting subtitle info {cid}",
                    headers=self._HEADERS,
                ),
                "Bilibili player subtitle API",
            )
            if player_data.get("need_login_subtitle"):
                self.report_warning(
                    f"Subtitles are only available when logged in. {self._login_hint()}",
                    only_once=True,
                )
            subtitle_data = player_data.get("subtitle")
            if subtitle_data is None:
                tracks = []
            elif isinstance(subtitle_data, dict):
                tracks = subtitle_data.get("subtitles") or []
            else:
                raise PublicCaptionLookupError(
                    "Bilibili player subtitle API returned invalid subtitle data."
                )
            if not isinstance(tracks, list):
                raise PublicCaptionLookupError(
                    "Bilibili player subtitle API returned invalid subtitle tracks."
                )
            for track in tracks:
                if (
                    not isinstance(track, dict)
                    or not isinstance(track.get("lan"), str)
                    or not isinstance(track.get("subtitle_url"), str)
                ):
                    raise PublicCaptionLookupError(
                        "Bilibili player subtitle API returned an invalid track."
                    )
                subtitle_url = _secure_subtitle_url(track["subtitle_url"])
                if subtitle_url is None:
                    raise PublicCaptionLookupError(
                        "Bilibili extractor subtitle URL violates the HTTPS host boundary."
                    )
                try:
                    srt = bilibili_json_to_srt(fetch_json(subtitle_url))
                except (KeyError, TypeError, ValueError) as error:
                    raise PublicCaptionLookupError(
                        "Bilibili extractor returned an invalid caption document."
                    ) from error
                if srt:
                    subtitles.setdefault(track["lan"], []).append(
                        {"ext": "srt", "data": srt}
                    )
            return subtitles

    BoundedBilibiliIE.__name__ = base_class.__name__
    BoundedBilibiliIE.__qualname__ = base_class.__qualname__
    return BoundedBilibiliIE


def _install_bounded_bilibili_extractor(ydl) -> None:
    from yt_dlp.extractor.bilibili import BiliBiliIE

    extractor_type = _bounded_bilibili_extractor_class(
        BiliBiliIE,
        _download_public_json,
    )
    ydl.add_info_extractor(extractor_type())


def _add_public_subtitles(url: str, info: dict, fetch_json) -> None:
    entries = _public_caption_entries(info)
    if entries and all(
        _has_chinese_caption_data(target) for _parent, target in entries
    ):
        return
    bvid = parse_bv_url(url).bvid
    view_data = _api_data(
        fetch_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"),
        "Bilibili view API",
    )
    aid = view_data.get("aid")
    raw_pages = view_data.get("pages")
    if not isinstance(aid, int) or not isinstance(raw_pages, list):
        raise PublicCaptionLookupError("Bilibili view API returned invalid pages.")
    pages = {}
    for page in raw_pages:
        if (
            not isinstance(page, dict)
            or not isinstance(page.get("page"), int)
            or not isinstance(page.get("cid"), int)
        ):
            raise PublicCaptionLookupError("Bilibili view API returned an invalid page.")
        pages[page["page"]] = page
    for default_part, (parent, target) in enumerate(entries, start=1):
        if _has_chinese_caption_data(target):
            continue
        match = re.search(r"_p([1-9]\d*)$", str(parent.get("id") or ""))
        part_number = int(match.group(1)) if match else default_part
        page = pages.get(part_number)
        if page is None:
            raise PublicCaptionLookupError(
                f"Bilibili view API has no page {part_number}."
            )
        dm_data = _api_data(
            fetch_json(
                "https://api.bilibili.com/x/v2/dm/view"
                f"?type=1&oid={page['cid']}&pid={aid}"
            ),
            f"Bilibili dm/view API for part {part_number}",
        )
        subtitle_data = dm_data.get("subtitle")
        if not isinstance(subtitle_data, dict):
            raise PublicCaptionLookupError(
                f"Bilibili dm/view API returned invalid subtitle data for part {part_number}."
            )
        tracks = subtitle_data.get("subtitles")
        if tracks is None:
            tracks = []
        if not isinstance(tracks, list) or not all(
            isinstance(track, dict) for track in tracks
        ):
            raise PublicCaptionLookupError(
                f"Bilibili dm/view API returned invalid subtitle tracks for part {part_number}."
            )
        ordered_tracks = _ordered_public_tracks(tracks)
        if _has_usable_caption_data(target):
            ordered_tracks = [
                track
                for track in ordered_tracks
                if _is_chinese_language(track["lan"])
            ]
        for track in ordered_tracks:
            subtitle_url = _secure_subtitle_url(track["subtitle_url"])
            if subtitle_url is None:
                continue
            try:
                srt = bilibili_json_to_srt(fetch_json(subtitle_url))
            except (KeyError, TypeError, ValueError) as error:
                raise PublicCaptionLookupError(
                    f"Bilibili returned an invalid caption document for part {part_number}."
                ) from error
            if not srt:
                continue
            target.setdefault("subtitles", {})[track["lan"]] = [
                {"ext": "srt", "data": srt}
            ]
            break


def _materialize_entries(info: dict) -> None:
    entries = info.get("entries")
    if entries is not None:
        entries = list(entries)
        info["entries"] = entries
        for entry in entries:
            if entry is not None:
                _materialize_entries(entry)


def _contains_cookie_error(error: BaseException, cookie_error_type) -> bool:
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, cookie_error_type):
            return True
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def fetch_info(
    url: str,
    *,
    ydl_factory=None,
    cookie_error_type=(),
    warn=None,
    public_json_fetcher=None,
    use_browser_cookies=False,
    playlist_end: int | None = 21,
) -> dict:
    parsed_url = parse_bv_url(url)
    use_public_fallback = ydl_factory is None or public_json_fetcher is not None
    install_bounded_extractor = ydl_factory is None
    if ydl_factory is None:
        from yt_dlp import YoutubeDL
        from yt_dlp.cookies import CookieLoadError

        ydl_factory = YoutubeDL
        cookie_error_type = CookieLoadError

    base_options = {
        "skip_download": True,
        "extract_flat": False,
        "writesubtitles": True,
    }
    if parsed_url.page is not None:
        base_options["playlist_items"] = str(parsed_url.page)
    elif playlist_end is not None:
        if playlist_end < 1:
            raise ValueError("playlist_end must be positive or None.")
        base_options["playlistend"] = playlist_end

    def extract(options: dict) -> dict:
        with ydl_factory(options) as ydl:
            if install_bounded_extractor:
                _install_bounded_bilibili_extractor(ydl)
            info = ydl.extract_info(url, download=False)
            _materialize_entries(info)
            return info

    if not use_browser_cookies:
        info = extract(base_options)
    else:
        try:
            info = extract({**base_options, "cookiesfrombrowser": ("chrome",)})
        except Exception as error:
            if not _contains_cookie_error(error, cookie_error_type):
                raise
            message = "Chrome cookies could not be read; retrying without browser login."
            if warn is None:
                print(f"Warning: {message}", file=sys.stderr)
            else:
                warn(message)
            info = extract(base_options)

    if use_public_fallback:
        _add_public_subtitles(
            url,
            info,
            public_json_fetcher or _download_public_json,
        )
    return info
