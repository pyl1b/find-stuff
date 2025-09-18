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
    envvar="LEROPA_LOG_FILE",
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
