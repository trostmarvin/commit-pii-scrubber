from __future__ import annotations

import datetime
from pathlib import Path
from typing import Callable, Optional, TypeVar

import questionary
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .discovery import (
    clone_github,
    discover_local,
    github_skip_reason,
    list_github,
)
from .gitutil import GitError, global_identity
from .models import PiiSpec, PushResult, Repo, RewriteResult, ScanResult
from .push import push_repo
from .rewrite import rewrite_repo
from .scan import scan_repo

app = typer.Typer(add_completion=False)
console = Console()


def main() -> None:
    app()


class Aborted(Exception):
    pass


def _ask(prompt_obj):
    """questionary returns None on Ctrl-C / EOF — treat that as an abort."""
    answer = prompt_obj.ask()
    if answer is None:
        raise Aborted()
    return answer


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


# --- step 1: PII spec ----------------------------------------------------------


def resolve_pii(
    emails: list[str],
    names: list[str],
    new_name: Optional[str],
    new_email: Optional[str],
    interactive: bool,
) -> PiiSpec:
    if not emails and not names:
        if not interactive:
            raise typer.BadParameter(
                "provide at least one --email or --name (required with --yes)"
            )
        emails = _split_csv(
            _ask(questionary.text("Emails to scrub (comma-separated, empty for none):"))
        )
        names = _split_csv(
            _ask(questionary.text("Names to scrub (comma-separated, empty for none):"))
        )
        if not emails and not names:
            raise typer.BadParameter("nothing to search for — need an email or a name")

    git_name, git_email = global_identity()
    if new_name is None:
        if interactive:
            new_name = _ask(
                questionary.text("Replacement name:", default=git_name or "")
            ).strip()
        else:
            new_name = git_name or ""
    if new_email is None:
        if interactive:
            new_email = _ask(
                questionary.text("Replacement email:", default=git_email or "")
            ).strip()
        else:
            new_email = git_email or ""
    if not new_name or not new_email:
        raise typer.BadParameter("replacement name and email are both required")
    return PiiSpec(emails=emails, names=names, new_name=new_name, new_email=new_email)


# --- step 2: source ------------------------------------------------------------


def resolve_source(
    github: bool,
    path: Optional[Path],
    include_forks: bool,
    include_archived: bool,
    limit: int,
    workdir: Optional[Path],
    interactive: bool,
) -> list[Repo]:
    if github and path:
        raise typer.BadParameter("--github and --path are mutually exclusive")
    if not github and path is None:
        if not interactive:
            raise typer.BadParameter("provide --github or --path (required with --yes)")
        choice = _ask(
            questionary.select(
                "Where should I look for repos?",
                choices=[
                    questionary.Choice("All my GitHub repos (via gh)", value="github"),
                    questionary.Choice("A local folder (searched recursively)", value="local"),
                ],
            )
        )
        if choice == "github":
            github = True
        else:
            path = Path(
                _ask(questionary.path("Folder to scan:", default=str(Path.cwd())))
            ).expanduser()

    if github:
        return _discover_github(include_forks, include_archived, limit, workdir)

    path = path.expanduser().resolve()
    if not path.is_dir():
        raise typer.BadParameter(f"not a directory: {path}")
    console.print(f"Scanning [bold]{path}[/bold] for git repositories…")
    repos = discover_local(path)
    console.print(f"Found [bold]{len(repos)}[/bold] repositories.\n")
    return repos


def _discover_github(
    include_forks: bool, include_archived: bool, limit: int, workdir: Optional[Path]
) -> list[Repo]:
    console.print("Listing your GitHub repositories via [bold]gh[/bold]…")
    entries = list_github(include_forks, include_archived, limit)
    if len(entries) >= limit:
        console.print(
            f"[yellow]Warning:[/yellow] hit --limit {limit}; the list may be truncated."
        )

    usable: list[dict] = []
    for entry in entries:
        reason = github_skip_reason(entry)
        if reason:
            console.print(f"  [dim]skipping {entry['nameWithOwner']}: {reason}[/dim]")
        else:
            usable.append(entry)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    workdir = (
        workdir or Path.home() / ".cache" / "pii-scrub" / f"run-{stamp}"
    ).expanduser()
    workdir.mkdir(parents=True, exist_ok=True)
    console.print(
        f"Cloning [bold]{len(usable)}[/bold] repositories into {workdir} …"
    )
    repos: list[Repo] = []
    with console.status("") as status:
        for i, entry in enumerate(usable, 1):
            status.update(f"[{i}/{len(usable)}] cloning {entry['nameWithOwner']}")
            try:
                repos.append(clone_github(entry, workdir))
            except GitError as exc:
                console.print(
                    f"  [red]failed to clone {entry['nameWithOwner']}:[/red] {exc}"
                )
    console.print(f"Cloned [bold]{len(repos)}[/bold] repositories.\n")
    return repos


# --- step 3: scan report ---------------------------------------------------------


def print_scan_table(results: list[ScanResult]) -> None:
    table = Table(title="PII scan results", show_lines=False)
    table.add_column("Repo", overflow="fold")
    table.add_column("Author", justify="right")
    table.add_column("Committer", justify="right")
    table.add_column("Msg (email)", justify="right")
    table.add_column("Msg (name)", justify="right")
    table.add_column("Matched identities", overflow="fold")
    table.add_column("Notes", overflow="fold")

    for res in results:
        identities = ", ".join(
            f"{n} <{e}>" for n, e in sorted(res.identity_pairs)
        )
        notes = "; ".join(res.repo.notes)
        style = None if res.matched else "dim"
        table.add_row(
            res.repo.name,
            str(res.author_hits),
            str(res.committer_hits),
            str(res.message_email_hits),
            str(res.message_name_hits),
            identities,
            notes,
            style=style,
        )
    console.print(table)


# --- steps 4-6: rewrite + push -----------------------------------------------


T = TypeVar("T")


def select_repos(
    prompt: str,
    candidates: list[T],
    label: Callable[[T], str],
    yes: bool,
    checked: bool = True,
) -> list[T]:
    if yes:
        return list(candidates)
    choices = [
        questionary.Choice(label(item), value=item, checked=checked)
        for item in candidates
    ]
    return _ask(
        questionary.checkbox(f"{prompt}  (<a> toggles all, <space> toggles one)", choices=choices)
    )


def print_caveats(backup_dir: Optional[Path], workdir_hint: bool) -> None:
    lines = [
        "• Every rewritten commit got a NEW SHA — collaborators must re-clone (or hard-reset).",
        "• GPG/SSH commit signatures were stripped; rewritten history is unsigned.",
        "• Open pull requests based on old SHAs will break or show as outdated.",
        "• Forks and GitHub's cached refs (refs/pull/*, direct SHA URLs) STILL contain the",
        "  old commits. For a full purge, ask GitHub Support to run a GC on the repo.",
    ]
    if backup_dir:
        lines.append(f"• Backups (original history): {backup_dir}  — restore: git clone <bundle>")
    if workdir_hint:
        lines.append("• GitHub clones were kept (they hold the rewrite until pushed).")
    console.print(Panel("\n".join(lines), title="Read this", border_style="red"))


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"pii-scrub {__version__}")
        raise typer.Exit()


@app.command()
def scrub(
    emails: list[str] = typer.Option(
        [], "--email", "-e", help="Email to scrub (repeatable)."
    ),
    names: list[str] = typer.Option(
        [], "--name", "-n", help="Name to scrub (repeatable)."
    ),
    new_name: Optional[str] = typer.Option(
        None, "--new-name", help="Replacement name (default: git config user.name)."
    ),
    new_email: Optional[str] = typer.Option(
        None, "--new-email", help="Replacement email (default: git config user.email)."
    ),
    scrub_names_in_messages: bool = typer.Option(
        False,
        "--scrub-names-in-messages",
        help="Also replace names inside commit messages (risky: names can be plain words).",
    ),
    github: bool = typer.Option(
        False, "--github", help="Scan all your GitHub repos via gh."
    ),
    path: Optional[Path] = typer.Option(
        None, "--path", help="Recursively scan a local folder for git repos."
    ),
    include_forks: bool = typer.Option(False, "--include-forks"),
    include_archived: bool = typer.Option(False, "--include-archived"),
    limit: int = typer.Option(2000, "--limit", help="Max GitHub repos to list."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Scan and report only; no rewrite, no push."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="No prompts: rewrite and push everything that matched."
    ),
    no_push: bool = typer.Option(False, "--no-push", help="Rewrite only, never push."),
    workdir: Optional[Path] = typer.Option(
        None, "--workdir", help="Where GitHub clones go (default: ~/.cache/pii-scrub)."
    ),
    backup_dir: Optional[Path] = typer.Option(
        None,
        "--backup-dir",
        help="Where backup bundles go (default: ~/.local/share/pii-scrub/backups).",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """Find PII (names/emails) in git commit history, rewrite it out with
    git-filter-repo, and force-push the cleaned history."""
    try:
        _scrub_flow(
            emails,
            names,
            new_name,
            new_email,
            scrub_names_in_messages,
            github,
            path,
            include_forks,
            include_archived,
            limit,
            dry_run,
            yes,
            no_push,
            workdir,
            backup_dir,
        )
    except (Aborted, KeyboardInterrupt):
        console.print("\n[red]Aborted.[/red] Nothing beyond the steps already reported was done.")
        raise typer.Exit(code=130)


def _scrub_flow(
    emails: list[str],
    names: list[str],
    new_name: Optional[str],
    new_email: Optional[str],
    scrub_names_in_messages: bool,
    github: bool,
    path: Optional[Path],
    include_forks: bool,
    include_archived: bool,
    limit: int,
    dry_run: bool,
    yes: bool,
    no_push: bool,
    workdir: Optional[Path],
    backup_dir: Optional[Path],
) -> None:
    interactive = not yes

    pii = resolve_pii(emails, names, new_name, new_email, interactive)
    console.print(
        f"\nSearching for: "
        f"{', '.join(pii.emails + pii.names) or '(nothing)'}"
        f"  →  replacing with [bold]{pii.new_name} <{pii.new_email}>[/bold]\n"
    )

    repos = resolve_source(
        github, path, include_forks, include_archived, limit, workdir, interactive
    )
    if not repos:
        console.print("No repositories found.")
        return

    # Scan
    results: list[ScanResult] = []
    with console.status("") as status:
        for i, repo in enumerate(repos, 1):
            status.update(f"[{i}/{len(repos)}] scanning {repo.name}")
            results.append(scan_repo(repo, pii))
    print_scan_table(results)

    if dry_run:
        console.print("\n[bold]--dry-run:[/bold] stopping after the scan.")
        return

    matched = [r for r in results if r.needs_rewrite(scrub_names_in_messages)]
    if not matched:
        console.print("\n[green]No repository contains the given PII — nothing to do.[/green]")
        return

    blocked = [r for r in matched if r.repo.rewrite_blocked]
    for res in blocked:
        console.print(
            f"[yellow]{res.repo.name}[/yellow] cannot be rewritten: {res.repo.rewrite_blocked}"
        )
    rewritable = [r for r in matched if not r.repo.rewrite_blocked]
    if not rewritable:
        console.print("\nNo rewritable repositories left.")
        return

    console.print()
    to_rewrite: list[ScanResult] = select_repos(
        "Rewrite history in:",
        rewritable,
        lambda r: f"{r.repo.name}  ({r.author_hits}a/{r.committer_hits}c/{r.message_email_hits}m)",
        yes,
    )
    if not to_rewrite:
        console.print("Nothing selected — stopping.")
        return

    backup_dir = (
        backup_dir or Path.home() / ".local" / "share" / "pii-scrub" / "backups"
    ).expanduser()

    # Rewrite
    rewrites: list[RewriteResult] = []
    for i, res in enumerate(to_rewrite, 1):
        console.print(f"[{i}/{len(to_rewrite)}] rewriting [bold]{res.repo.name}[/bold] …")
        rw = rewrite_repo(res.repo, res, pii, backup_dir, scrub_names_in_messages)
        rewrites.append(rw)
        if rw.ok:
            verified = "[green]verified clean[/green]" if rw.verified_clean else "[red]STILL DIRTY — inspect manually[/red]"
            console.print(f"    done ({verified}); backup: {rw.bundle_path}")
        else:
            console.print(f"    [red]failed:[/red] {rw.error}")
            if rw.bundle_path:
                console.print(f"    backup (pre-rewrite state): {rw.bundle_path}")

    # Push
    pushed: list[PushResult] = []
    pushable = [rw for rw in rewrites if rw.ok and rw.repo.origin_url]
    skipped_no_origin = [rw for rw in rewrites if rw.ok and not rw.repo.origin_url]
    for rw in skipped_no_origin:
        console.print(f"[dim]{rw.repo.name}: no origin remote — nothing to push.[/dim]")

    if pushable and not no_push:
        console.print()
        selected: list[RewriteResult] = select_repos(
            "Force-push rewritten history to origin for:",
            pushable,
            lambda rw: f"{rw.repo.name}  →  {rw.repo.origin_url}",
            yes,
        )
        if selected and not yes:
            if not _ask(
                questionary.confirm(
                    f"Force-push {len(selected)} repo(s)? This rewrites the remote history irreversibly.",
                    default=False,
                )
            ):
                selected = []
        for rw in selected:
            console.print(f"pushing [bold]{rw.repo.name}[/bold] …")
            pr = push_repo(rw.repo)
            pushed.append(pr)
            if pr.ok:
                console.print("    [green]branches + tags pushed.[/green]")
            else:
                console.print(f"    [red]push failed:[/red] {pr.error}")
                if pr.protected_branch:
                    console.print(
                        "    [yellow]Hint:[/yellow] a protected branch rejected the force-push. "
                        "Temporarily allow force pushes (GitHub → Settings → Branches), rerun, then re-protect."
                    )

    # Summary
    console.print()
    ok_rw = sum(1 for rw in rewrites if rw.ok)
    console.print(
        f"[bold]Summary:[/bold] {len(results)} scanned, {len(matched)} matched, "
        f"{ok_rw}/{len(rewrites)} rewritten, {sum(1 for p in pushed if p.ok)}/{len(pushed)} pushed."
    )
    if rewrites:
        print_caveats(backup_dir, workdir_hint=any(r.repo.source == "github" for r in rewrites))


if __name__ == "__main__":
    main()
