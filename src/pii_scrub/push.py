from __future__ import annotations

from .gitutil import git
from .models import PushResult, Repo

_PROTECTED_MARKERS = ("protected branch", "gh006")


def push_repo(repo: Repo) -> PushResult:
    """Force-push all branches and tags. --force-with-lease is useless here:
    filter-repo removed the remote-tracking refs, so there is no lease."""
    result = PushResult(repo=repo)
    errors: list[str] = []
    for what, flag in (("branches", "--all"), ("tags", "--tags")):
        proc = git(repo.path, "push", "--force", "origin", flag, check=False)
        ok = proc.returncode == 0
        if what == "branches":
            result.branches_ok = ok
        else:
            result.tags_ok = ok
        if not ok:
            stderr = (proc.stderr or proc.stdout).strip()
            errors.append(f"{what}: {stderr}")
            if any(m in stderr.lower() for m in _PROTECTED_MARKERS):
                result.protected_branch = True
    if errors:
        result.error = "; ".join(errors)
    return result
