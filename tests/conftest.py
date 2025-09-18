import os
import subprocess
from pathlib import Path
from typing import Any, Generator, List

import pytest


def _list_tracked_files_for_repo(repo_root: Path) -> List[str]:
    """Return git-tracked file list by scanning the working tree.

    This mock approximates `git ls-files` by listing all files under the repo
    root, excluding the `.git` directory, and returning relative POSIX paths.
    """
    tracked: List[str] = []

    # Walk the directory tree and collect files, excluding `.git`.
    for root, dirnames, filenames in os.walk(repo_root):
        # Skip .git directories when descending
        dirnames[:] = [d for d in dirnames if d != ".git"]

        base = Path(root)
        for fname in filenames:
            fpath = base / fname
            rel = fpath.relative_to(repo_root).as_posix()
            tracked.append(rel)

    # Sort for deterministic output
    tracked.sort()
    return tracked


@pytest.fixture(autouse=True)
def mock_git_subprocess_run(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Autouse fixture to mock git commands to avoid interactive prompts.

    It intercepts subprocess.run calls where the command starts with `git` and
    simulates the subset of subcommands used by tests:
    - git init
    - git add -A
    - git config ...
    - git commit -m ...
    - git ls-files [-z]

    All other subprocess.run calls are forwarded to the real implementation.
    """

    real_run = subprocess.run

    def _build_completed(
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> subprocess.CompletedProcess:
        # Construct a CompletedProcess-like object compatible with code under
        # test.
        return subprocess.CompletedProcess(
            args=[],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def fake_run(*pargs: Any, **pkwargs: Any) -> subprocess.CompletedProcess:
        # Determine argv and forward non-git commands
        if pargs:
            argv = pargs[0]
        else:
            argv = pkwargs.get("args")

        try:
            argv_list = list(argv) if argv is not None else []
        except TypeError:
            # Non-iterable first arg; delegate to real run
            return real_run(*pargs, **pkwargs)

        if not argv_list or argv_list[0] != "git":
            return real_run(*pargs, **pkwargs)

        # Resolve repo root from cwd if provided
        cwd = pkwargs.get("cwd")
        repo_root = Path(cwd) if cwd is not None else Path.cwd()

        subcmd = argv_list[1:]  # after 'git'

        # git init
        if subcmd[:1] == ["init"]:
            (repo_root / ".git").mkdir(parents=True, exist_ok=True)
            return _build_completed(
                stdout=b"Initialized empty Git repository\n",
            )

        # git add -A (no-op)
        if subcmd[:2] == ["add", "-A"]:
            return _build_completed()

        # git config ... (no-op)
        if subcmd[:1] == ["config"]:
            return _build_completed()

        # git commit -m ... (no-op)
        if subcmd[:1] == ["commit"]:
            return _build_completed(stdout=b"[mock] commit\n")

        # git ls-files [-z]
        if subcmd and subcmd[0] == "ls-files":
            use_zero = "-z" in subcmd
            files = _list_tracked_files_for_repo(repo_root)
            if use_zero:
                payload = (
                    "\x00".join(files) + ("\x00" if files else "")
                ).encode("utf-8")
            else:
                payload = ("\n".join(files) + ("\n" if files else "")).encode(
                    "utf-8"
                )
            return _build_completed(stdout=payload)

        # Unknown git command: succeed with empty output
        return _build_completed()

    monkeypatch.setattr(subprocess, "run", fake_run)

    yield
