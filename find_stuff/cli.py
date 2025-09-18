import logging
from pathlib import Path
from typing import Optional, Tuple

import click
from dotenv import load_dotenv  # type: ignore[import-not-found]
from InquirerPy.prompts.fuzzy import FuzzyPrompt
from InquirerPy.prompts.list import ListPrompt as SelectPrompt
from InquirerPy.utils import InquirerPyStyle, get_style
from sqlalchemy import select
from sqlalchemy.orm import Session

# Initialize Colorama to ensure ANSI codes work on Windows terminals
# Import dynamically to avoid type-stub issues in linting environments
try:  # pragma: no cover
    import importlib

    _cm = importlib.import_module("colorama")
    Fore = _cm.Fore  # type: ignore[assignment]
    Style = _cm.Style  # type: ignore[assignment]
    colorama_init = _cm.init  # type: ignore[assignment]
except Exception:  # pragma: no cover

    class _NoColor:
        def __getattr__(self, _: str) -> str:
            return ""

    Fore = _NoColor()  # type: ignore[assignment]
    Style = _NoColor()  # type: ignore[assignment]

    # Keep signature simple to satisfy linters formatting
    def colorama_init(*_: object, **__: object) -> None:
        return None


from find_stuff.__version__ import __version__
from find_stuff.indexing import add_to_index, rebuild_index, search_files
from find_stuff.models import File as SAFile
from find_stuff.models import create_engine_for_path
from find_stuff.navigation import (
    DirEntry,
    FileEntry,
    RepoEntry,
    file_status,
    list_repo_dir_contents,
    list_repositories,
    open_in_code,
)


@click.group()
@click.option(
    "--debug/--no-debug", default=False, help="Enable verbose debug logging."
)
@click.option(
    "--trace/--no-trace", default=False, help="Enable trace level logging."
)
@click.option(
    "--log-file",
    type=click.Path(file_okay=True, dir_okay=False),
    envvar="FIND_STUFF_LOG_FILE",
    help=("Path to write log output to instead of stderr."),
)
@click.version_option(__version__, prog_name="find_stuff")
def cli(debug: bool, trace: bool, log_file: Optional[str] = None) -> None:
    """Configure logging and load environment variables."""
    # Ensure Colorama is initialized so ANSI colors render on Windows
    try:
        colorama_init(autoreset=True)
    except Exception:
        pass
    if trace:
        level = 1
    elif debug:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        filename=log_file,
        level=level,
        format="[%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if trace:
        logging.debug("Trace mode is on")
    if debug:
        logging.debug("Debug mode is on")
    load_dotenv()


@cli.command(name="rebuild-index")
@click.argument(
    "root", type=click.Path(file_okay=False, dir_okay=True, exists=True)
)
@click.option(
    "--db",
    "db_path",
    type=click.Path(
        file_okay=True, dir_okay=False, writable=True, path_type=Path
    ),
    default=Path(".find_stuff/index.sqlite3"),
    show_default=True,
    help="Path to the SQLite index database.",
)
@click.option(
    "--ext",
    "exts",
    multiple=True,
    help=(
        "File extension to include (repeatable). "
        "May be given with or without leading dot. Default: py"
    ),
)
def cli_rebuild_index(root: str, db_path: Path, exts: Tuple[str, ...]) -> None:
    """Rebuild the index for git-tracked Python files under ROOT.

    Args:
        root: Directory to scan recursively for repositories.
        db_path: Path to the SQLite database to (re)build.
        exts: One or more file extensions to include.
    """

    root_path = Path(root)
    exts_list = list(exts) if exts else ["py"]
    click.echo(
        (
            "Rebuilding index from "
            f"{root_path} into {db_path} for *.{', *.'.join(exts_list)} ..."
        )
    )
    rebuild_index(root_path, db_path, file_types=exts_list)
    click.echo("Done.")


@cli.command(name="add-to-index")
@click.argument(
    "root", type=click.Path(file_okay=False, dir_okay=True, exists=True)
)
@click.option(
    "--db",
    "db_path",
    type=click.Path(
        file_okay=True, dir_okay=False, writable=True, path_type=Path
    ),
    default=Path(".find_stuff/index.sqlite3"),
    show_default=True,
    help="Path to the SQLite index database.",
)
@click.option(
    "--ext",
    "exts",
    multiple=True,
    help=(
        "File extension to include (repeatable). "
        "May be given with or without leading dot. Default: py"
    ),
)
def cli_add_to_index(root: str, db_path: Path, exts: Tuple[str, ...]) -> None:
    """Add repositories/files under ROOT into the existing index.

    This command preserves existing content in the database and only appends
    repositories that are not already present.
    """

    root_path = Path(root)
    exts_list = list(exts) if exts else ["py"]
    click.echo(
        (
            "Adding to index from "
            f"{root_path} into {db_path} for *.{', *.'.join(exts_list)} ..."
        )
    )
    add_to_index(root_path, db_path, file_types=exts_list)
    click.echo("Done.")


@cli.command(name="search")
@click.option(
    "--db",
    "db_path",
    type=click.Path(
        file_okay=True, dir_okay=False, readable=True, path_type=Path
    ),
    default=Path(".find_stuff/index.sqlite3"),
    show_default=True,
    help="Path to the SQLite index database.",
)
@click.option(
    "--any",
    "require_all",
    flag_value=False,
    help="Match if any term is present.",
)
@click.option(
    "--all",
    "require_all",
    flag_value=True,
    default=True,
    help="Match only files containing all terms.",
)
@click.option(
    "--regex/--no-regex",
    default=False,
    help="Treat terms as regular expressions.",
)
@click.option(
    "--case-sensitive/--ignore-case",
    default=False,
    help="Case sensitive matching.",
)
@click.option(
    "--limit",
    type=int,
    default=50,
    show_default=True,
    help="Maximum number of results.",
)
@click.option(
    "--ext",
    "exts",
    multiple=True,
    help=(
        "File extension to include (repeatable). "
        "May be given with or without leading dot. If omitted, "
        "no extension filter is applied."
    ),
)
@click.argument("terms", nargs=-1, required=True)
def cli_search(
    db_path: Path,
    require_all: bool,
    regex: bool,
    case_sensitive: bool,
    limit: int,
    terms: Tuple[str, ...],
    exts: Tuple[str, ...],
) -> None:
    """Search indexed files for TERMS.

    Args:
        db_path: SQLite database path.
        require_all: If True, require all terms; if False, any.
        regex: Treat terms as regex patterns.
        case_sensitive: Use case-sensitive matching.
        limit: Max number of results.
        terms: Search terms.
    """

    results = search_files(
        db_path,
        list(terms),
        limit=limit,
        require_all_terms=require_all,
        regex=regex,
        case_sensitive=case_sensitive,
        file_types=list(exts) if exts else None,
    )

    for path, score in results:
        click.echo(f"{score}\t{path}")


@cli.command(name="file-info")
@click.option(
    "--db",
    "db_path",
    type=click.Path(
        file_okay=True, dir_okay=False, readable=True, path_type=Path
    ),
    default=Path(".find_stuff/index.sqlite3"),
    show_default=True,
    help="Path to the SQLite index database.",
)
@click.argument("file_path", type=click.Path(exists=True, path_type=Path))
def cli_file_info(db_path: Path, file_path: Path) -> None:
    """Show stored metadata for FILE_PATH and verify if it changed.

    The command looks up the file by absolute path in the index, prints the
    stored metadata (size, mtime, ctime, sha256_hex) using human-readable
    times and compares with the current filesystem values. If the time fields
    indicate change but the hash matches, or vice-versa, both aspects are
    reported for clarity.
    """

    engine = create_engine_for_path(db_path)
    with Session(engine) as session:
        row = session.execute(
            select(
                SAFile.relpath,
                SAFile.abspath,
                SAFile.size_bytes,
                SAFile.mtime_ns,
                SAFile.ctime_ns,
                SAFile.sha256_hex,
            ).where(SAFile.abspath == str(file_path.resolve()))
        ).first()

        if row is None:
            click.echo("Not found in index.")
            return

        relpath, abspath, size_b, mt_ns, ct_ns, digest = row

        click.echo(_c("Stored:", fg="cyan", bold=True))
        click.echo(f"  {_c('path:', 'cyan', True)} {abspath}")
        click.echo(f"  {_c('relpath:', 'cyan', True)} {relpath}")
        click.echo(f"  {_c('size_bytes:', 'cyan', True)} {size_b}")
        click.echo(
            f"  {_c('mtime:', 'cyan', True)} {_format_ns_as_local(mt_ns)}"
        )
        click.echo(
            f"  {_c('ctime:', 'cyan', True)} {_format_ns_as_local(ct_ns)}"
        )
        click.echo(f"  {_c('sha256_hex:', 'cyan', True)} {digest}")

        # Compute current values
        try:
            st = file_path.stat()
            cur_size = int(st.st_size)
            cur_mtime_ns = int(
                getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
            )
            cur_ctime_ns = int(
                getattr(st, "st_ctime_ns", int(st.st_ctime * 1_000_000_000))
            )
            # Hash only if size or times differ; may still hash to be sure
            import hashlib

            h = hashlib.sha256()
            with file_path.open("rb") as rf:
                for chunk in iter(lambda: rf.read(1024 * 1024), b""):
                    h.update(chunk)
            cur_digest = h.hexdigest()
        except Exception as exc:  # pragma: no cover
            click.echo(f"Error reading current file state: {exc}")
            return

        click.echo(_c("Current:", fg="cyan", bold=True))
        click.echo(f"  {_c('size_bytes:', 'cyan', True)} {cur_size}")
        click.echo(
            f"  {_c('mtime:', 'cyan', True)}"
            f" {_format_ns_as_local(cur_mtime_ns)}"
        )
        click.echo(
            f"  {_c('ctime:', 'cyan', True)}"
            f" {_format_ns_as_local(cur_ctime_ns)}"
        )
        click.echo(f"  {_c('sha256_hex:', 'cyan', True)} {cur_digest}")

        # Determine change status
        time_changed = (mt_ns != cur_mtime_ns) or (ct_ns != cur_ctime_ns)
        hash_changed = (digest or "") != cur_digest

        if not time_changed and not hash_changed:
            click.echo(_c("Status: unchanged", fg="green", bold=True))
            return

        if time_changed and hash_changed:
            click.echo(
                _c(
                    "Status: modified (time and hash differ)",
                    fg="red",
                    bold=True,
                )
            )
            return

        if time_changed and not hash_changed:
            click.echo(
                _c(
                    "Status: time changed but content hash is identical "
                    "(likely touch)",
                    fg="yellow",
                    bold=True,
                )
            )
            return

        if hash_changed and not time_changed:
            click.echo(
                _c(
                    "Status: content hash changed but times are same "
                    "(clock or copy?)",
                    fg="yellow",
                    bold=True,
                )
            )
            return


def _clear_screen() -> None:
    """Clear the terminal screen in a cross-platform manner."""

    import os

    try:
        if os.name == "nt":
            os.system("cls")
        else:
            os.system("clear")
    except Exception:
        pass


def _read_key() -> Optional[str]:
    """Read a single key from the keyboard.

    Returns simple strings: 'UP', 'DOWN', 'ENTER', 'BACKSPACE', single chars,
    or None if not supported.
    """

    try:
        import msvcrt  # type: ignore

        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == "\x08":
            return "BACKSPACE"
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            if ch2 == "H":
                return "UP"
            if ch2 == "P":
                return "DOWN"
            return None
        return ch
    except Exception:
        return None


def _prompt(text: str) -> str:
    """Prompt for input robustly."""

    try:
        return input(text)
    except EOFError:
        return ""
    except KeyboardInterrupt:
        return ""


def _print_header(title: str, subtitle: Optional[str] = None) -> None:
    """Print a section header."""

    click.echo(_c(title, fg="cyan", bold=True))
    if subtitle:
        click.echo(subtitle)
    click.echo("")


def _render_list(title: str, items: Tuple[str, ...], selected: int) -> None:
    """Render simple list UI."""

    _clear_screen()
    _print_header(title)
    for idx, line in enumerate(items, start=1):
        prefix = "> " if (idx - 1) == selected else "  "
        click.echo(f"{prefix}{line}")
    click.echo("")
    click.echo(
        "Arrows: navigate  Enter: select  b: back  i: input  c: open in Code"
    )
    click.echo("q: quit")


def _fmt_repo_line(entry: RepoEntry) -> str:
    return f"{entry.index}. {entry.name}\t{entry.root}"


def _fmt_dir_line(base: Path, entry: DirEntry) -> str:
    full = base / entry.name
    return f"{entry.index}. {entry.name}\t{full}"


def _fmt_file_line(entry: FileEntry) -> str:
    return f"{entry.index}. {entry.name}\t{entry.path}"


def _format_ns_as_local(ns: Optional[int]) -> str:
    """Convert a nanosecond timestamp to a human-readable local datetime.

    Args:
        ns: Nanoseconds since the Unix epoch. Can be None.

    Returns:
        Human-readable local datetime like "YYYY-MM-DD HH:MM:SS" or "N/A".
    """

    try:
        if ns is None or int(ns) <= 0:
            return "N/A"
        from datetime import datetime

        seconds = int(ns) / 1_000_000_000
        dt = datetime.fromtimestamp(seconds)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "N/A"


_COLOR_FORCE_DISABLE: bool = False


def _colors_supported() -> bool:
    """Return True if ANSI colors are likely supported for stdout."""

    try:
        import os
        import sys

        if _COLOR_FORCE_DISABLE:
            return False
        if os.environ.get("NO_COLOR") is not None:
            return False
        term = os.environ.get("TERM", "")
        if term.lower() == "dumb":
            return False
        return bool(getattr(sys.stdout, "isatty", lambda: False)())
    except Exception:
        return False


def _c(text: str, fg: Optional[str] = None, bold: bool = False) -> str:
    """Colorize text using Colorama if supported; otherwise return as-is."""

    if not _colors_supported() or (fg is None and not bold):
        return text

    color_map = {
        "black": Fore.BLACK,
        "red": Fore.RED,
        "green": Fore.GREEN,
        "yellow": Fore.YELLOW,
        "blue": Fore.BLUE,
        "magenta": Fore.MAGENTA,
        "cyan": Fore.CYAN,
        "white": Fore.WHITE,
        # bright variants (if used)
        "bright_black": Fore.LIGHTBLACK_EX,
        "bright_red": Fore.LIGHTRED_EX,
        "bright_green": Fore.LIGHTGREEN_EX,
        "bright_yellow": Fore.LIGHTYELLOW_EX,
        "bright_blue": Fore.LIGHTBLUE_EX,
        "bright_magenta": Fore.LIGHTMAGENTA_EX,
        "bright_cyan": Fore.LIGHTCYAN_EX,
        "bright_white": Fore.LIGHTWHITE_EX,
    }

    parts: list[str] = []
    if bold:
        parts.append(Style.BRIGHT)
    if fg is not None:
        parts.append(color_map.get(fg.lower(), ""))
    parts.append(text)
    parts.append(Style.RESET_ALL)
    return "".join(parts)


def _prompt_style() -> InquirerPyStyle:
    """Return InquirerPy style for prompts.

    Avoids embedding ANSI codes directly in prompt strings.
    """

    # prompt_toolkit style strings; use ANSI color names for portability
    style_dict = {
        # Pointer on the highlighted line
        "pointer": "ansicyan bold",
        # Marker (mainly for multiselect; keep visible)
        "marker": "ansimagenta bold",
        # Matched characters in fuzzy filter
        "fuzzy_match": "ansiyellow bold",
        # The small prompt before input in fuzzy prompt
        "fuzzy_prompt": "ansicyan bold",
        # Info segment (e.g., counts)
        "fuzzy_info": "ansiwhite",
        # Instruction/help text
        "instruction": "ansiwhite",
        # Question/message styling (applies in some prompts)
        "question": "ansicyan bold",
        "questionmark": "ansicyan bold",
        # Highlighted choice (used by some list prompts)
        "highlighted": "ansicyan bold",
    }

    # Merge with InquirerPy defaults to preserve unspecified styles
    return get_style(style_dict, style_override=False)


@cli.command(name="browse")
@click.option(
    "--db",
    "db_path",
    type=click.Path(
        file_okay=True, dir_okay=False, readable=True, path_type=Path
    ),
    default=Path(".find_stuff/index.sqlite3"),
    show_default=True,
    help="Path to the SQLite index database.",
)
@click.option(
    "--color/--no-color",
    default=True,
    help="Enable or disable ANSI colors in the browse command.",
)
def cli_browse(db_path: Path, color: bool) -> None:
    """Interactively browse indexed repositories.

    Use the fuzzy selector to filter by name as you type. Choose directories to
    descend, files to view status, and actions to open in VS Code, change
    repository, or quit.
    """

    repos = list_repositories(db_path)
    if not repos:
        click.echo("No repositories in the database.")
        return

    current_repo: Optional[RepoEntry] = None
    rel_dir: str = ""

    prev_disable = _COLOR_FORCE_DISABLE
    try:
        # Apply requested color preference for this session
        globals()["_COLOR_FORCE_DISABLE"] = not color

        while True:
            if current_repo is None:
                repo_choices: list[dict[str, object]] = [
                    {"name": r.name, "value": ("repo", r)} for r in repos
                ]
                repo_choices.append({"name": "Quit", "value": ("quit", None)})
                sel_kind, sel_payload = FuzzyPrompt(
                    message="Select repository",
                    choices=repo_choices,
                    instruction=(
                        "Type to filter, Up/Down to navigate, Enter to select"
                    ),
                    style=_prompt_style(),
                ).execute()
                if sel_kind == "quit":
                    return
                if sel_kind == "repo":
                    current_repo = sel_payload
                    rel_dir = ""
                    continue

            assert current_repo is not None
            base = (
                current_repo.root / rel_dir if rel_dir else current_repo.root
            )
            dirs, files = list_repo_dir_contents(
                db_path, current_repo.root, rel_dir
            )

            choices: list[dict[str, object]] = []
            if rel_dir:
                choices.append(
                    {
                        "name": ".. (parent)",
                        "value": ("parent", None),
                    }
                )
            for d in dirs:
                prefix = "[D]"
                choices.append(
                    {
                        "name": f"{prefix} {d.name}",
                        "value": ("dir", d),
                    }
                )
            for f in files:
                prefix_f = "[F]"
                choices.append(
                    {
                        "name": f"{prefix_f} {f.name}",
                        "value": ("file", f),
                    }
                )
            choices.extend(
                [
                    {
                        "name": "Open this directory in VS Code",
                        "value": ("open", base),
                    },
                    {
                        "name": "Change repository",
                        "value": ("change_repo", None),
                    },
                    {"name": "Quit", "value": ("quit", None)},
                ]
            )

            kind2, payload2 = FuzzyPrompt(
                message=f"{current_repo.name} / {rel_dir or '.'}",
                choices=choices,
                instruction=(
                    "Type to filter, Up/Down to navigate, Enter to select"
                ),
                style=_prompt_style(),
            ).execute()

            if kind2 == "quit":
                return
            if kind2 == "change_repo":
                current_repo = None
                rel_dir = ""
                continue
            if kind2 == "parent":
                rel_path = Path(rel_dir).parent
                rel_dir = "" if str(rel_path) == "." else rel_path.as_posix()
                continue
            if kind2 == "dir":
                rel_dir = (
                    f"{rel_dir}/{payload2.name}" if rel_dir else payload2.name
                )
                continue
            if kind2 == "open":
                ok, msg = open_in_code(payload2)  # type: ignore[arg-type]
                if not ok:
                    click.echo(msg)
                continue
            if kind2 == "file":
                fentry: FileEntry = payload2
                st = file_status(db_path, fentry.path)
                _clear_screen()
                _print_header("File info", str(fentry.path))
                colored_flag = _c(
                    str(st.in_index),
                    "green" if st.in_index else "red",
                    True,
                )
                click.echo(f"{_c('in_index:', 'cyan', True)} {colored_flag}")
                click.echo(
                    f"{_c('stored size_bytes:', 'cyan', True)} {st.size_bytes}"
                )
                click.echo(
                    f"{_c('stored mtime:', 'cyan', True)} "
                    f"{_format_ns_as_local(st.mtime_ns)}"
                )
                click.echo(
                    f"{_c('stored ctime:', 'cyan', True)} "
                    f"{_format_ns_as_local(st.ctime_ns)}"
                )
                click.echo(
                    f"{_c('stored sha256_hex:', 'cyan', True)} {st.sha256_hex}"
                )
                click.echo(
                    f"{_c('current size_bytes:', 'cyan', True)} "
                    f"{st.current_size_bytes}"
                )
                click.echo(
                    f"{_c('current mtime:', 'cyan', True)} "
                    f"{_format_ns_as_local(st.current_mtime_ns)}"
                )
                click.echo(
                    f"{_c('current ctime:', 'cyan', True)} "
                    f"{_format_ns_as_local(st.current_ctime_ns)}"
                )
                click.echo(
                    f"{_c('current sha256_hex:', 'cyan', True)} "
                    f"{st.current_sha256_hex}"
                )
                click.echo(f"{_c('status:', 'cyan', True)} {st.status}")
                click.echo("")

                action = SelectPrompt(
                    message="Action",
                    choices=[
                        {"name": "Open in VS Code", "value": "open"},
                        {"name": "Back", "value": "back"},
                    ],
                    default="back",
                    style=_prompt_style(),
                ).execute()
                if action == "open":
                    ok, msg = open_in_code(fentry.path)
                    if not ok:
                        click.echo(msg)
                continue

    finally:
        globals()["_COLOR_FORCE_DISABLE"] = prev_disable

    # End while
