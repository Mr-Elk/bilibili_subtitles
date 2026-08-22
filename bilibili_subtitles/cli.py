import argparse
from datetime import datetime
from pathlib import Path
import sys

from .core import InvalidBilibiliUrl
from .reader import ReaderInputError, read_transcripts
from .workflow import (
    NoSubtitlesError,
    PartSelectionRequiredError,
    extract_to_markdown,
)
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
        description=(
            "Extract existing Bilibili captions to timestamped Markdown, "
            "then read bounded local slices."
        ),
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="Direct HTTPS bilibili.com/video/BV... URL (for extract action)",
    )
    parser.add_argument(
        "--action",
        choices=("extract", "status", "inventory", "map", "search", "slice"),
        default="extract",
        help="Operation to perform (default: extract)",
    )
    parser.add_argument("--target", type=Path, help="Transcript file or BV directory")
    parser.add_argument("--query", help="Search text")
    parser.add_argument("--start", default="00:00:00", help="Slice start HH:MM:SS")
    parser.add_argument("--end", help="Slice end HH:MM:SS")
    parser.add_argument("--context", type=int, default=1, help="Search context cues")
    parser.add_argument(
        "--max-results", type=int, default=8, help="Maximum search matches"
    )
    parser.add_argument(
        "--chunk-chars", type=int, default=5_000, help="Map target chunk size"
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=0,
        help="Maximum printed characters (0 uses the action default)",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
        help="Local read output format (default: text)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "output",
        help="Output parent directory (default: project output directory)",
    )
    part_group = parser.add_mutually_exclusive_group()
    part_group.add_argument(
        "--page",
        type=int,
        help="Extract one anthology part (positive integer)",
    )
    part_group.add_argument(
        "--all-parts",
        action="store_true",
        help="Explicitly allow extraction of every part in a large anthology",
    )
    parser.add_argument(
        "--max-parts",
        type=int,
        default=20,
        help="Maximum anthology parts before an explicit choice is required (default: 20)",
    )
    cookie_group = parser.add_mutually_exclusive_group()
    cookie_group.add_argument(
        "--use-browser-cookies",
        action="store_true",
        help="Explicitly read Chrome login state for captions that require login",
    )
    cookie_group.add_argument(
        "--no-browser-cookies",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    if args.action != "extract":
        if args.target is None:
            print(f"Error: {args.action} requires --target.", file=stderr)
            return 2
        try:
            rendered = read_transcripts(
                args.action,
                target=args.target,
                query=args.query,
                start=args.start,
                end=args.end,
                context=args.context,
                max_results=args.max_results,
                chunk_chars=args.chunk_chars,
                max_chars=args.max_chars,
                output_format=args.output_format,
            )
        except ReaderInputError as error:
            print(f"Error: {error}", file=stderr)
            return 2
        except Exception as error:
            print(f"Error: {error}", file=stderr)
            return 1
        if rendered:
            print(rendered, file=stdout)
        return 0

    if args.output_format != "text":
        print("Error: --format is only supported for local read actions.", file=stderr)
        return 2
    if not args.url:
        print("Error: extract requires a direct Bilibili BV URL.", file=stderr)
        return 2
    if args.max_parts < 1:
        print("Error: --max-parts must be a positive integer.", file=stderr)
        return 2
    if info_fetcher is None:
        info_fetcher = lambda url: fetch_info(
            url,
            use_browser_cookies=args.use_browser_cookies,
            playlist_end=None if args.all_parts else args.max_parts + 1,
        )
    clock = clock or (lambda: datetime.now().astimezone())

    try:
        result = extract_to_markdown(
            args.url,
            output_root=args.output_root,
            fetch_info=info_fetcher,
            extracted_at=clock().isoformat(timespec="seconds"),
            page=args.page,
            all_parts=args.all_parts,
            max_parts=args.max_parts,
        )
    except (InvalidBilibiliUrl, PartSelectionRequiredError) as error:
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
