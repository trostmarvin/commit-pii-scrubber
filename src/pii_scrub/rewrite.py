from __future__ import annotations

import datetime
import shutil
import sys
import tempfile
from pathlib import Path

from .gitutil import GitError, git, origin_url, run
from .models import PiiSpec, Repo, RewriteResult, ScanResult
from .scan import verify_repo


def filter_repo_cmd() -> list[str]:
    """Locate git-filter-repo. Under pipx the console script lives in the
    venv's bin (not on PATH), so try next to the interpreter first."""
    exe = Path(sys.executable).parent / "git-filter-repo"
    if exe.exists():
        return [str(exe)]
    found = shutil.which("git-filter-repo")
    if found:
        return [found]
    return [sys.executable, "-m", "git_filter_repo"]


def make_bundle(repo: Repo, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = repo.name.replace("/", "__").replace(" ", "_")
    bundle = backup_dir / f"{safe_name}-{stamp}.bundle"
    git(repo.path, "bundle", "create", str(bundle), "--all")
    return bundle


def write_mailmap(scan: ScanResult, pii: PiiSpec, tmpdir: Path) -> Path:
    lines = [
        f"{pii.new_name} <{pii.new_email}> {old_name} <{old_email}>"
        for old_name, old_email in sorted(scan.identity_pairs)
    ]
    path = tmpdir / "mailmap"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_replace_message(pii: PiiSpec, scrub_names: bool, tmpdir: Path) -> Path:
    lines = [f"{email}==>{pii.new_email}" for email in pii.emails]
    if scrub_names:
        lines += [f"{name}==>{pii.new_name}" for name in pii.names]
    path = tmpdir / "replace-message"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def rewrite_repo(
    repo: Repo,
    scan: ScanResult,
    pii: PiiSpec,
    backup_dir: Path,
    scrub_names_in_messages: bool,
) -> RewriteResult:
    result = RewriteResult(repo=repo)
    if repo.rewrite_blocked:
        result.error = repo.rewrite_blocked
        return result
    try:
        result.bundle_path = make_bundle(repo, backup_dir)
        # filter-repo deletes the origin remote; capture the URL first.
        origin = origin_url(repo.path) or repo.origin_url

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd = [*filter_repo_cmd(), "--mailmap", str(write_mailmap(scan, pii, tmpdir))]
            if scan.message_email_hits or (
                scrub_names_in_messages and scan.message_name_hits
            ):
                cmd += [
                    "--replace-message",
                    str(write_replace_message(pii, scrub_names_in_messages, tmpdir)),
                ]
            if repo.source == "local":
                # Local repos are not fresh clones; the bundle above is the safety net.
                cmd.append("--force")
            run(cmd, cwd=repo.path)

        if origin:
            git(repo.path, "remote", "add", "origin", origin, check=False)
            repo.origin_url = origin

        result.verified_clean = verify_repo(repo, pii, scrub_names_in_messages)
        result.ok = True
    except (GitError, OSError) as exc:
        result.error = str(exc)
    return result
