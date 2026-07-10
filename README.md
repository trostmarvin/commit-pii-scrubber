# commit-pii-scrubber

`pii-scrub` scans git repositories for PII (your name and/or email) in commit
history — author/committer identity fields **and** commit message bodies — and
rewrites it out with [git-filter-repo], then force-pushes the cleaned history
after your confirmation.

## Install

```bash
pipx install .          # or: uv tool install .
```

`git` and (for GitHub mode) an authenticated `gh` CLI must be on your PATH.
`git-filter-repo` is bundled as a Python dependency.

## Usage

Fully interactive — just run it and answer the prompts:

```bash
pii-scrub
```

Or non-interactive:

```bash
# Scan every repo you own on GitHub (fresh clones, safest for filter-repo):
pii-scrub --github -e old@example.com -n "Old Name" --dry-run

# Scan a local folder recursively and rewrite, but never push:
pii-scrub --path ~/projects -e old@example.com --no-push

# Full auto (no prompts, pushes everything that matched):
pii-scrub --github -e old@example.com --yes
```

Key flags:

| Flag | Meaning |
|---|---|
| `-e/--email`, `-n/--name` | PII to search for (repeatable; at least one required) |
| `--new-name`, `--new-email` | Replacement identity (default: your global git config) |
| `--github` / `--path DIR` | Source: all your GitHub repos, or a local folder |
| `--scrub-names-in-messages` | Also replace names inside commit messages (off by default — names can be ordinary words) |
| `--dry-run` | Scan and report only |
| `-y/--yes` | No prompts: rewrite and push everything that matched |
| `--no-push` | Rewrite only, never push |
| `--include-forks`, `--include-archived` | Widen the GitHub repo set |
| `--limit N` | Max GitHub repos to list (default 2000) |
| `--backup-dir`, `--workdir` | Where bundles / GitHub clones go |
| `--version` | Print the version and exit |

## How it works

1. Discovers repos (`gh repo list` + fresh clones, or a recursive folder walk).
2. Scans all refs in one pass (`git log --all`) and reports hits per repo.
3. For each repo you confirm: writes a `git bundle` backup, generates a
   mailmap from the *actual* matched `(name, email)` pairs plus a
   replace-message file for emails in message bodies, runs `git filter-repo`,
   and re-adds the origin remote that filter-repo removes.
4. Verifies by re-scanning, then shows **one** checkbox prompt to pick which
   repos to force-push (`<a>` selects all), with a final confirmation.

## Read this before pushing

- Every rewritten commit gets a **new SHA** — collaborators must re-clone.
- **GPG/SSH signatures are stripped**; rewritten history is unsigned.
- Open PRs based on old SHAs break.
- **Forks and GitHub's cached refs (`refs/pull/*`, direct SHA URLs) still
  contain the old commits.** For a full purge, contact GitHub Support and ask
  them to GC the repository.
- Backups of the original history are kept as `.bundle` files
  (default `~/.local/share/pii-scrub/backups`); restore with
  `git clone <bundle> restored-repo`.

[git-filter-repo]: https://github.com/newren/git-filter-repo
