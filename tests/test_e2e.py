"""End-to-end tests: throwaway repo + local bare 'remote', all inside tmp_path."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pii_scrub.cli import app
from pii_scrub.discovery import discover_local
from pii_scrub.models import PiiSpec
from pii_scrub.push import push_repo
from pii_scrub.rewrite import rewrite_repo
from pii_scrub.scan import scan_repo, verify_repo

OLD_NAME = "Old Name"
OLD_EMAIL = "old@pii.test"
CLEAN_NAME = "Clean Coworker"
CLEAN_EMAIL = "clean@example.test"
NEW_NAME = "New Name"
NEW_EMAIL = "new@clean.test"

PII = PiiSpec(
    emails=[OLD_EMAIL], names=[OLD_NAME], new_name=NEW_NAME, new_email=NEW_EMAIL
)


def run_git(cwd: Path, *args: str, env: dict | None = None) -> str:
    full_env = None
    if env:
        import os

        full_env = {**os.environ, **env}
    proc = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, env=full_env
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


def identity_env(name: str, email: str) -> dict:
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


@pytest.fixture
def rig(tmp_path: Path) -> dict:
    """Bare remote + working clone seeded with PII and clean commits + a tag."""
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(remote))
    work = tmp_path / "work"
    run_git(tmp_path, "clone", str(remote), str(work))

    def commit(fname: str, message: str, name: str, email: str) -> None:
        (work / fname).write_text(message)
        run_git(work, "add", fname)
        run_git(work, "commit", "-m", message, env=identity_env(name, email))

    commit("a.txt", "initial commit", OLD_NAME, OLD_EMAIL)
    commit("b.txt", f"mention my address {OLD_EMAIL} in the message", OLD_NAME, OLD_EMAIL)
    commit("c.txt", "a clean commit by someone else", CLEAN_NAME, CLEAN_EMAIL)
    commit("d.txt", f"thanks {OLD_NAME} for the review", CLEAN_NAME, CLEAN_EMAIL)
    run_git(work, "tag", "v1.0")
    run_git(work, "push", "origin", "--all")
    run_git(work, "push", "origin", "--tags")
    return {"remote": remote, "work": work, "root": tmp_path}


def all_history(repo: Path) -> str:
    return run_git(repo, "log", "--all", "--format=%an %ae %cn %ce %B")


def test_scan_finds_pii(rig):
    repos = discover_local(rig["root"])
    work = next(r for r in repos if r.path == rig["work"])
    res = scan_repo(work, PII)
    assert res.author_hits == 2
    assert res.committer_hits == 2
    assert res.message_email_hits == 1
    assert res.message_name_hits == 1  # only the "thanks Old Name" commit
    assert (OLD_NAME, OLD_EMAIL) in res.identity_pairs
    assert res.total_commits == 4


def test_rewrite_and_push_scrubs_remote(rig, tmp_path):
    repos = discover_local(rig["root"])
    work = next(r for r in repos if r.path == rig["work"])
    scan = scan_repo(work, PII)
    backup_dir = tmp_path / "backups"

    rw = rewrite_repo(work, scan, PII, backup_dir, scrub_names_in_messages=False)
    assert rw.ok, rw.error
    assert rw.verified_clean
    assert rw.bundle_path and rw.bundle_path.exists()
    run_git(rig["work"], "bundle", "verify", str(rw.bundle_path))

    # origin restored by rewrite_repo
    assert work.origin_url == str(rig["remote"])
    pr = push_repo(work)
    assert pr.ok, pr.error

    # verify in a FRESH clone of the bare remote
    check = tmp_path / "check"
    run_git(tmp_path, "clone", str(rig["remote"]), str(check))
    history = all_history(check)
    assert OLD_EMAIL not in history
    assert f"{OLD_NAME} <" not in history  # identity gone
    assert NEW_EMAIL in history and NEW_NAME in history
    # clean author untouched
    assert CLEAN_EMAIL in history and CLEAN_NAME in history
    # name in message body kept (scrub_names_in_messages=False)
    assert f"thanks {OLD_NAME} for the review" in history
    # tag survived
    assert "v1.0" in run_git(check, "tag")


def test_rewrite_scrubs_names_in_messages(rig, tmp_path):
    repos = discover_local(rig["root"])
    work = next(r for r in repos if r.path == rig["work"])
    scan = scan_repo(work, PII)
    rw = rewrite_repo(work, scan, PII, tmp_path / "b", scrub_names_in_messages=True)
    assert rw.ok and rw.verified_clean
    history = all_history(rig["work"])
    assert OLD_NAME not in history
    assert f"thanks {NEW_NAME} for the review" in history


def test_dirty_repo_refused(rig, tmp_path):
    (rig["work"] / "dirty.txt").write_text("uncommitted")
    repos = discover_local(rig["root"])
    work = next(r for r in repos if r.path == rig["work"])
    assert work.rewrite_blocked
    scan = scan_repo(work, PII)
    rw = rewrite_repo(work, scan, PII, tmp_path / "b", False)
    assert not rw.ok
    assert "dirty" in rw.error
    assert rw.bundle_path is None  # refused before any action


def test_no_match_repo_skipped(rig):
    repos = discover_local(rig["root"])
    work = next(r for r in repos if r.path == rig["work"])
    other = PiiSpec(
        emails=["nobody@nowhere.test"], names=[], new_name="X", new_email="x@y.test"
    )
    res = scan_repo(work, other)
    assert not res.matched
    assert not res.needs_rewrite(False)


def test_no_origin_repo_has_note(tmp_path):
    lone = tmp_path / "lone"
    run_git(tmp_path, "init", str(lone))
    (lone / "f.txt").write_text("x")
    run_git(lone, "add", "f.txt")
    run_git(lone, "commit", "-m", "c", env=identity_env(OLD_NAME, OLD_EMAIL))
    repos = discover_local(tmp_path)
    repo = next(r for r in repos if r.path == lone)
    assert repo.origin_url is None
    assert any("no origin" in n for n in repo.notes)


def test_cli_dry_run_changes_nothing(rig):
    refs_before = run_git(rig["remote"], "for-each-ref")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--path",
            str(rig["root"]),
            "--email",
            OLD_EMAIL,
            "--new-name",
            NEW_NAME,
            "--new-email",
            NEW_EMAIL,
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    assert run_git(rig["remote"], "for-each-ref") == refs_before
    assert not list(rig["root"].glob("**/*.bundle"))


def test_cli_yes_end_to_end(rig, tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--path",
            str(rig["root"]),
            "--email",
            OLD_EMAIL,
            "--name",
            OLD_NAME,
            "--new-name",
            NEW_NAME,
            "--new-email",
            NEW_EMAIL,
            "--backup-dir",
            str(tmp_path / "bundles"),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    check = tmp_path / "check2"
    run_git(tmp_path, "clone", str(rig["remote"]), str(check))
    history = all_history(check)
    assert OLD_EMAIL not in history
    assert NEW_EMAIL in history
    assert list((tmp_path / "bundles").glob("*.bundle"))
