import logging
from pathlib import Path
from typing import Optional, Tuple

import click
from dotenv import load_dotenv  # type: ignore[import-not-found]
from sqlalchemy import select
from sqlalchemy.orm import Session

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
    resolve_dir_by_input,
    resolve_file_by_input,
    resolve_repo_by_input,
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
    help="Path to write log output to instead of stderr.",
)
@click.version_option(__version__, prog_name="find_stuff")
def cli(debug: bool, trace: bool, log_file: Optional[str] = None) -> None:
    """Configure logging and load environment variables."""
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
    stored metadata (size, mtime_ns, ctime_ns, sha256_hex) and compares with
    the current filesystem values. If the time fields indicate change but the
    hash matches, or vice-versa, both aspects are reported for clarity.
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

        click.echo("Stored:")
        click.echo(f"  path: {abspath}")
        click.echo(f"  relpath: {relpath}")
        click.echo(f"  size_bytes: {size_b}")
        click.echo(f"  mtime_ns: {mt_ns}")
        click.echo(f"  ctime_ns: {ct_ns}")
        click.echo(f"  sha256_hex: {digest}")

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

        click.echo("Current:")
        click.echo(f"  size_bytes: {cur_size}")
        click.echo(f"  mtime_ns: {cur_mtime_ns}")
        click.echo(f"  ctime_ns: {cur_ctime_ns}")
        click.echo(f"  sha256_hex: {cur_digest}")

        # Determine change status
        time_changed = (mt_ns != cur_mtime_ns) or (ct_ns != cur_ctime_ns)
        hash_changed = (digest or "") != cur_digest

        if not time_changed and not hash_changed:
            click.echo("Status: unchanged")
            return

        if time_changed and hash_changed:
            click.echo("Status: modified (time and hash differ)")
            return

        if time_changed and not hash_changed:
            click.echo(
                "Status: time changed but content hash is identical "
                "(likely touch)"
            )
            return

        if hash_changed and not time_changed:
            click.echo(
                "Status: content hash changed but times are same "
                "(clock or copy?)"
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

    click.echo(title)
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
def cli_browse(db_path: Path) -> None:
    """Interactively browse repositories, directories, and files.

    Use Up/Down to navigate and Enter to select. Press 'i' to type an index,
    name or path (quotes allowed). Press 'b' to go to the parent, 'c' to open
    the selected item in VS Code, and 'q' to quit.
    """

    # State machine: level = 'repos' or 'dir'
    level = "repos"
    selected = 0
    repos = list_repositories(db_path)
    if not repos:
        click.echo("No repositories in the database.")
        return
    current_repo: Optional[RepoEntry] = None
    rel_dir: str = ""

    while True:
        try:
            if level == "repos":
                lines = tuple(_fmt_repo_line(r) for r in repos)
                if selected >= len(repos):
                    selected = 0
                _render_list(
                    "Repositories (c: open repo in Code)", lines, selected
                )

                key = _read_key()
                if key is None:
                    # Fallback to prompt mode
                    text = _prompt("Enter index/name/path (q to quit): ")
                    if text.lower() in {"q", "quit", ":q"}:
                        return
                    if not text.strip():
                        continue
                    resolved = resolve_repo_by_input(repos, text)
                    if resolved is None:
                        click.echo("Not found.")
                        _prompt("Press Enter...")
                        continue
                    current_repo = resolved
                    level = "dir"
                    selected = 0
                    rel_dir = ""
                    continue

                if key == "q":
                    return
                if key == "UP":
                    selected = (selected - 1) % len(repos)
                    continue
                if key == "DOWN":
                    selected = (selected + 1) % len(repos)
                    continue
                if key == "i":
                    text = _prompt("Enter index/name/path: ")
                    resolved = resolve_repo_by_input(repos, text)
                    if resolved is None:
                        continue
                    current_repo = resolved
                    level = "dir"
                    selected = 0
                    rel_dir = ""
                    continue
                if key == "c":
                    if repos:
                        ok, msg = open_in_code(repos[selected].root)
                        if not ok:
                            click.echo(msg)
                            _prompt("Press Enter...")
                    continue
                if key == "ENTER":
                    current_repo = repos[selected]
                    level = "dir"
                    selected = 0
                    rel_dir = ""
                    continue

            # Directory level inside current_repo
            assert current_repo is not None
            base = (
                current_repo.root / rel_dir if rel_dir else current_repo.root
            )
            dirs, files = list_repo_dir_contents(
                db_path, current_repo.root, rel_dir
            )
            combined = [("D", d, _fmt_dir_line(base, d)) for d in dirs] + [
                ("F", f, _fmt_file_line(f)) for f in files
            ]
            if not combined:
                # Empty dir; allow back
                combined = []
            if selected >= len(combined):
                selected = 0

            _render_list(
                f"{current_repo.name} \\ {rel_dir or '.'} (c: open in Code)",
                tuple(line for _t, _e, line in combined),
                selected,
            )

            key = _read_key()
            if key is None:
                text = _prompt("Enter index/name/path (b: back, q: quit): ")
                if text.lower() in {"q", "quit", ":q"}:
                    return
                if text.lower() in {"b", "back"}:
                    if rel_dir:
                        # Up one level
                        rel_dir = str(Path(rel_dir).parent).replace("\\", "/")
                        if rel_dir == ".":
                            rel_dir = ""
                        selected = 0
                        continue
                    level = "repos"
                    selected = 0
                    continue
                # Resolve directory or file by input
                # Try directory names first
                resolved_d = resolve_dir_by_input(dirs, text)
                if resolved_d is not None:
                    rel_dir = (
                        f"{rel_dir}/{resolved_d.name}"
                        if rel_dir
                        else resolved_d.name
                    )
                    selected = 0
                    continue
                resolved_f = resolve_file_by_input(files, text)
                if resolved_f is not None:
                    st = file_status(db_path, resolved_f.path)
                    _clear_screen()
                    _print_header("File info", str(resolved_f.path))
                    click.echo(f"in_index: {st.in_index}")
                    click.echo(f"stored size_bytes: {st.size_bytes}")
                    click.echo(f"stored mtime_ns: {st.mtime_ns}")
                    click.echo(f"stored ctime_ns: {st.ctime_ns}")
                    click.echo(f"stored sha256_hex: {st.sha256_hex}")
                    click.echo(f"current size_bytes: {st.current_size_bytes}")
                    click.echo(f"current mtime_ns: {st.current_mtime_ns}")
                    click.echo(f"current ctime_ns: {st.current_ctime_ns}")
                    click.echo(f"current sha256_hex: {st.current_sha256_hex}")
                    click.echo(f"status: {st.status}")
                    click.echo("")
                    choice = _prompt(
                        "Press c to open in Code, Enter to go back: "
                    )
                    if choice.lower() == "c":
                        ok, msg = open_in_code(resolved_f.path)
                        if not ok:
                            click.echo(msg)
                            _prompt("Press Enter...")
                    continue
                # Not found
                click.echo("Not found.")
                _prompt("Press Enter...")
                continue

            if key == "q":
                return
            if key in {"b", "BACKSPACE"}:
                if rel_dir:
                    rel_dir = str(Path(rel_dir).parent).replace("\\", "/")
                    if rel_dir == ".":
                        rel_dir = ""
                    selected = 0
                else:
                    level = "repos"
                    selected = 0
                continue
            if key == "UP":
                selected = (selected - 1) % max(1, len(combined))
                continue
            if key == "DOWN":
                if combined:
                    selected = (selected + 1) % len(combined)
                continue
            if key == "i":
                text = _prompt("Enter index/name/path: ")
                # Try dir then file
                resolved_d = resolve_dir_by_input(dirs, text)
                if resolved_d is not None:
                    rel_dir = (
                        f"{rel_dir}/{resolved_d.name}"
                        if rel_dir
                        else resolved_d.name
                    )
                    selected = 0
                    continue
                resolved_f = resolve_file_by_input(files, text)
                if resolved_f is not None:
                    st = file_status(db_path, resolved_f.path)
                    _clear_screen()
                    _print_header("File info", str(resolved_f.path))
                    click.echo(f"in_index: {st.in_index}")
                    click.echo(f"stored size_bytes: {st.size_bytes}")
                    click.echo(f"stored mtime_ns: {st.mtime_ns}")
                    click.echo(f"stored ctime_ns: {st.ctime_ns}")
                    click.echo(f"stored sha256_hex: {st.sha256_hex}")
                    click.echo(f"current size_bytes: {st.current_size_bytes}")
                    click.echo(f"current mtime_ns: {st.current_mtime_ns}")
                    click.echo(f"current ctime_ns: {st.current_ctime_ns}")
                    click.echo(f"current sha256_hex: {st.current_sha256_hex}")
                    click.echo(f"status: {st.status}")
                    click.echo("")
                    choice = _prompt(
                        "Press c to open in Code, Enter to go back: "
                    )
                    if choice.lower() == "c":
                        ok, msg = open_in_code(resolved_f.path)
                        if not ok:
                            click.echo(msg)
                            _prompt("Press Enter...")
                    continue
                continue
            if key == "c":
                if not combined:
                    continue
                _t, _e, _ = combined[selected]
                target_path = (
                    (current_repo.root / _e.name) if _t == "D" else _e.path
                )
                ok, msg = open_in_code(target_path)
                if not ok:
                    click.echo(msg)
                    _prompt("Press Enter...")
                continue
            if key == "ENTER":
                if not combined:
                    continue
                kind, ent, _line = combined[selected]
                if kind == "D":
                    rel_dir = f"{rel_dir}/{ent.name}" if rel_dir else ent.name
                    selected = 0
                    continue
                # File selected -> show info
                st = file_status(db_path, ent.path)
                _clear_screen()
                _print_header("File info", str(ent.path))
                click.echo(f"in_index: {st.in_index}")
                click.echo(f"stored size_bytes: {st.size_bytes}")
                click.echo(f"stored mtime_ns: {st.mtime_ns}")
                click.echo(f"stored ctime_ns: {st.ctime_ns}")
                click.echo(f"stored sha256_hex: {st.sha256_hex}")
                click.echo(f"current size_bytes: {st.current_size_bytes}")
                click.echo(f"current mtime_ns: {st.current_mtime_ns}")
                click.echo(f"current ctime_ns: {st.current_ctime_ns}")
                click.echo(f"current sha256_hex: {st.current_sha256_hex}")
                click.echo(f"status: {st.status}")
                click.echo("")
                choice = _prompt("Press c to open in Code, Enter to go back: ")
                if choice.lower() == "c":
                    ok, msg = open_in_code(ent.path)
                    if not ok:
                        click.echo(msg)
                        _prompt("Press Enter...")
                continue
        except KeyboardInterrupt:  # pragma: no cover
            return
        except Exception as exc:  # pragma: no cover
            click.echo(f"Error: {exc}")
            _prompt("Press Enter to continue...")
