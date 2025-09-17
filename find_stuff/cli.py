import logging
from pathlib import Path
from typing import Optional
from find_stuff.__version__ import __version__
import click
from dotenv import load_dotenv  # type: ignore[import-not-found]
from typing import Tuple
from find_stuff.indexing import rebuild_index, search_files


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


@cli.command(name="py")
@click.argument("path")
@click.argument("query", nargs=-1)
def find_py_code(
    path: str,
    query: Tuple[str, ...],
) -> None:
    """Find Python code in a file or directory.

    Args:
        path: Path to the file or directory to search.
        query: Query to search for.
    """


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
def cli_rebuild_index(root: str, db_path: Path) -> None:
    """Rebuild the index for git-tracked Python files under ROOT.

    Args:
        root: Directory to scan recursively for repositories.
        db_path: Path to the SQLite database to (re)build.
    """

    root_path = Path(root)
    click.echo(f"Rebuilding index from {root_path} into {db_path} ...")
    rebuild_index(root_path, db_path)
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
@click.argument("terms", nargs=-1, required=True)
def cli_search(
    db_path: Path,
    require_all: bool,
    regex: bool,
    case_sensitive: bool,
    limit: int,
    terms: Tuple[str, ...],
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
    )

    for path, score in results:
        click.echo(f"{score}\t{path}")
