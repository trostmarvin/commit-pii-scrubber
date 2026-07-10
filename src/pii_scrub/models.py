from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class PiiSpec:
    """What to search for and what to replace it with."""

    emails: list[str]
    names: list[str]
    new_name: str
    new_email: str

    def __post_init__(self) -> None:
        self.emails = [e.strip().lower() for e in self.emails if e.strip()]
        self.names = [n.strip() for n in self.names if n.strip()]

    @property
    def has_any(self) -> bool:
        return bool(self.emails or self.names)


@dataclass
class Repo:
    name: str
    path: Path
    origin_url: str | None
    source: Literal["github", "local"]
    default_branch: str | None = None
    is_empty: bool = False
    notes: list[str] = field(default_factory=list)
    # Reason the rewrite is refused (dirty tree, shallow, ...); None = rewritable.
    rewrite_blocked: str | None = None


@dataclass
class ScanResult:
    repo: Repo
    # (name, email) pairs actually seen in history that matched the PII spec.
    # These drive mailmap generation, including name-only matches.
    identity_pairs: set[tuple[str, str]] = field(default_factory=set)
    author_hits: int = 0
    committer_hits: int = 0
    message_email_hits: int = 0
    message_name_hits: int = 0
    total_commits: int = 0

    @property
    def matched(self) -> bool:
        return (
            bool(self.identity_pairs)
            or self.message_email_hits > 0
            or self.message_name_hits > 0
        )

    def needs_rewrite(self, scrub_names_in_messages: bool) -> bool:
        return (
            bool(self.identity_pairs)
            or self.message_email_hits > 0
            or (scrub_names_in_messages and self.message_name_hits > 0)
        )


@dataclass
class RewriteResult:
    repo: Repo
    ok: bool = False
    bundle_path: Path | None = None
    error: str | None = None
    verified_clean: bool = False


@dataclass
class PushResult:
    repo: Repo
    branches_ok: bool = False
    tags_ok: bool = False
    protected_branch: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.branches_ok and self.tags_ok
