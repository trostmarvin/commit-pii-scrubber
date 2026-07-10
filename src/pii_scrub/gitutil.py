from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    def __init__(self, cmd: list, stderr: str):
        self.cmd = [str(c) for c in cmd]
        self.stderr = stderr
        super().__init__(f"`{' '.join(self.cmd)}` failed: {stderr.strip()}")


def run(args: list, cwd=None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [str(a) for a in args], cwd=cwd, text=True, capture_output=True
    )
    if check and proc.returncode != 0:
        raise GitError(args, proc.stderr or proc.stdout)
    return proc


def git(repo_path, *args, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=repo_path, check=check)


def has_commits(path) -> bool:
    return git(path, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def is_dirty(path) -> bool:
    proc = git(path, "status", "--porcelain", check=False)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def is_shallow(path) -> bool:
    proc = git(path, "rev-parse", "--is-shallow-repository", check=False)
    return proc.stdout.strip() == "true"


def is_bare(path) -> bool:
    proc = git(path, "rev-parse", "--is-bare-repository", check=False)
    return proc.stdout.strip() == "true"


def is_detached(path) -> bool:
    return git(path, "symbolic-ref", "-q", "HEAD", check=False).returncode != 0


def has_submodules(path) -> bool:
    return (Path(path) / ".gitmodules").is_file()


def origin_url(path) -> str | None:
    proc = git(path, "remote", "get-url", "origin", check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def global_identity() -> tuple[str | None, str | None]:
    name = run(["git", "config", "--global", "user.name"], check=False).stdout.strip()
    email = run(["git", "config", "--global", "user.email"], check=False).stdout.strip()
    return name or None, email or None
