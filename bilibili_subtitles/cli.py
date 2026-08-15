import argparse
from datetime import datetime
from pathlib import Path
import sys

from .core import InvalidBilibiliUrl
from .workflow import NoSubtitlesError, extract_to_markdown
from .yt_dlp_adapter import fetch_info


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(
    argv=None,
    *,
    info_fetcher=None,
    clock=None,
    stdout=None,
    stderr=None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Extract existing Bilibili captions to timestamped Markdown.",
    )
    parser.add_argument("url", help="Direct HTTPS bilibili.com/video/BV... URL")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "output",
        help="Output parent directory (default: project output directory)",
    )
    parser.add_argument(
        "--no-browser-cookies",
        action="store_true",
        help="Do not read browser cookies; use anonymous metadata and public captions",
    )
    args = parser.parse_args(argv)

    if info_fetcher is None:
        info_fetcher = lambda url: fetch_info(
            url,
            use_browser_cookies=not args.no_browser_cookies,
        )
    clock = clock or (lambda: datetime.now().astimezone())
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    try:
        result = extract_to_markdown(
            args.url,
            output_root=args.output_root,
            fetch_info=info_fetcher,
            extracted_at=clock().isoformat(timespec="seconds"),
        )
    except InvalidBilibiliUrl as error:
        print(f"Error: {error}", file=stderr)
        return 2
    except NoSubtitlesError as error:
        print(f"Error: {error}", file=stderr)
        return 1
    except Exception as error:
        print(f"Error: {error}", file=stderr)
        return 1
    print(
        f"Extracted {result.success_count} part(s); "
        f"{result.no_subtitle_count} part(s) had no captions.",
        file=stdout,
    )
    print(f"Output: {result.output_dir}", file=stdout)
    return 0
