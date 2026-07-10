from __future__ import annotations

import json
import os
from pathlib import Path

from .gitutil import (
    git,
    has_commits,
    has_submodules,
    is_bare,
    is_detached,
    is_dirty,
    is_shallow,
    origin_url,
    run,
)
from .models import Repo

GH_FIELDS = (
    "nameWithOwner,name,url,sshUrl,isFork,isArchived,isPrivate,isEmpty,"
    "defaultBranchRef,viewerPermission"
)
_PUSHABLE_PERMISSIONS = {"ADMIN", "MAINTAIN", "WRITE"}


# --- local mode --------------------------------------------------------------


def build_local_repo(path: Path, name: str) -> Repo:
    repo = Repo(name=name, path=path, origin_url=origin_url(path), source="local")
    repo.is_empty = not has_commits(path)
    bare = is_bare(path)
    if bare:
        repo.notes.append("bare repository")
    else:
        if is_dirty(path):
            repo.notes.append("dirty working tree")
            repo.rewrite_blocked = "dirty working tree — commit or stash first"
        if is_detached(path):
            repo.notes.append("detached HEAD")
            repo.rewrite_blocked = "detached HEAD — check out a branch first"
        if has_submodules(path):
            repo.notes.append("has submodules")
    if is_shallow(path):
        repo.notes.append("shallow clone")
        repo.rewrite_blocked = "shallow clone — run 'git fetch --unshallow' first"
    if repo.origin_url is None:
        repo.notes.append("no origin remote (nothing to push)")
    return repo


def discover_local(root: Path) -> list[Repo]:
    root = root.resolve()
    repos: list[Repo] = []
    for dirpath, dirnames, filenames in os.walk(root):
        p = Path(dirpath)
        if ".git" in filenames:
            # .git file = linked worktree or submodule checkout; the real repo
            # lives elsewhere, so skip it here.
            dirnames[:] = []
            continue
        if ".git" in dirnames:
            rel = p.relative_to(root)
            repos.append(build_local_repo(p, str(rel) if str(rel) != "." else p.name))
            dirnames[:] = []
            continue
        # Bare repo: no .git dir, but HEAD/objects/refs at top level.
        if "HEAD" in filenames and "objects" in dirnames and "refs" in dirnames:
            if is_bare(p):
                rel = p.relative_to(root)
                repos.append(
                    build_local_repo(p, str(rel) if str(rel) != "." else p.name)
                )
                dirnames[:] = []
                continue
        dirnames[:] = [d for d in dirnames if not d.startswith(".git")]
    return sorted(repos, key=lambda r: r.name.lower())


# --- GitHub mode --------------------------------------------------------------


def list_github(include_forks: bool, include_archived: bool, limit: int) -> list[dict]:
    cmd = ["gh", "repo", "list", "--json", GH_FIELDS, "--limit", str(limit)]
    if not include_forks:
        cmd.append("--source")
    if not include_archived:
        cmd.append("--no-archived")
    return json.loads(run(cmd).stdout)


def github_skip_reason(entry: dict) -> str | None:
    if entry.get("isEmpty"):
        return "empty repository"
    perm = entry.get("viewerPermission")
    if perm and perm not in _PUSHABLE_PERMISSIONS:
        return f"no push access (permission: {perm})"
    return None


def clone_github(entry: dict, workdir: Path) -> Repo:
    owner, name = entry["nameWithOwner"].split("/", 1)
    dest = workdir / f"{owner}__{name}"
    run(["gh", "repo", "clone", entry["nameWithOwner"], str(dest)])
    repo = Repo(
        name=entry["nameWithOwner"],
        path=dest,
        origin_url=origin_url(dest),
        source="github",
        default_branch=(entry.get("defaultBranchRef") or {}).get("name"),
    )
    repo.is_empty = not has_commits(dest)
    if entry.get("isFork"):
        repo.notes.append("fork — upstream/other forks keep the old commits")
    if entry.get("isArchived"):
        repo.notes.append("archived — GitHub rejects pushes until unarchived")
    if entry.get("isPrivate"):
        repo.notes.append("private")
    return repo
