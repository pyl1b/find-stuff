import importlib
import logging
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional
from find_stuff.__version__ import __version__
import click
import yaml  # type: ignore
from dotenv import load_dotenv  # type: ignore[import-not-found]
from typing import Tuple


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
