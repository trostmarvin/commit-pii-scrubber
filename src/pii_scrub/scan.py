from __future__ import annotations

from .gitutil import git
from .models import PiiSpec, Repo, ScanResult

FIELD_SEP = "\x1f"
RECORD_SEP = "\x1e"
# One record per commit; %B (raw body) goes last so stray field separators in a
# message can't shift the fixed fields before it.
LOG_FORMAT = (
    f"%H{FIELD_SEP}%an{FIELD_SEP}%ae{FIELD_SEP}%cn{FIELD_SEP}%ce{FIELD_SEP}%B{RECORD_SEP}"
)


def scan_repo(repo: Repo, pii: PiiSpec) -> ScanResult:
    """Single-pass scan of all refs. Author/committer matching is done in
    Python because `git log --author=X --committer=X` ANDs the filters."""
    result = ScanResult(repo=repo)
    if repo.is_empty:
        return result

    proc = git(repo.path, "log", "--all", f"--format={LOG_FORMAT}")
    emails = set(pii.emails)
    names = set(pii.names)

    for record in proc.stdout.split(RECORD_SEP):
        record = record.lstrip("\n")
        if not record.strip():
            continue
        parts = record.split(FIELD_SEP)
        if len(parts) < 6:
            continue
        _sha, a_name, a_email, c_name, c_email = parts[:5]
        body = FIELD_SEP.join(parts[5:])
        result.total_commits += 1

        if a_email.lower() in emails or a_name in names:
            result.author_hits += 1
            result.identity_pairs.add((a_name, a_email))
        if c_email.lower() in emails or c_name in names:
            result.committer_hits += 1
            result.identity_pairs.add((c_name, c_email))

        body_lower = body.lower()
        if any(e in body_lower for e in emails):
            result.message_email_hits += 1
        if any(n in body for n in names):
            result.message_name_hits += 1

    return result


def verify_repo(repo: Repo, pii: PiiSpec, scrub_names_in_messages: bool) -> bool:
    """Re-scan after a rewrite; True when no scrub-relevant PII remains."""
    res = scan_repo(repo, pii)
    if res.identity_pairs or res.message_email_hits:
        return False
    if scrub_names_in_messages and res.message_name_hits:
        return False
    return True
