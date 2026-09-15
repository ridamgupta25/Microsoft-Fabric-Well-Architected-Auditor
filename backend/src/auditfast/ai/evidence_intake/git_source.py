"""Fetch documentation files from a Git repo so checks can score from source control.

An alternative input to uploading files: point at a GitHub or Azure DevOps repo,
pull its documentation (Markdown/text and anything under a docs folder), and feed
it into the same evidence pipeline. The host is detected from the URL; the PAT is
used for one request only and never stored.

Read-only and best-effort: a single unreachable file is skipped, not fatal, and
the whole fetch is capped so a huge repo cannot exhaust memory.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

import requests

#: Only text-shaped documentation is pulled; code and binaries are ignored.
_DOC_EXTS = (".md", ".markdown", ".rst", ".txt")
#: Path fragments that mark documentation even without a doc extension.
_DOC_HINTS = ("readme", "docs/", "documentation", "runbook", "architecture", "governance")
_MAX_FILES = 60
_MAX_BYTES = 400_000  # per file
_TIMEOUT = 20


class GitFetchError(RuntimeError):
    """Raised when the repo cannot be read (auth, not found, unsupported host)."""


@dataclass(frozen=True, slots=True)
class GitRepo:
    """A repo to pull docs from. ``token`` is a PAT, kept out of reprs and logs."""

    url: str
    token: str = field(default="", repr=False)
    branch: str = ""


def _is_doc(path: str) -> bool:
    low = path.lower()
    return low.endswith(_DOC_EXTS) or any(h in low for h in _DOC_HINTS)


def detect_host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "github.com" in host or "githubusercontent.com" in host:
        return "github"
    if "dev.azure.com" in host or "visualstudio.com" in host:
        return "ado"
    return "unknown"


def fetch_docs(repo: GitRepo) -> list[tuple[str, bytes]]:
    """Return ``(path, bytes)`` for each documentation file in the repo."""
    host = detect_host(repo.url)
    if host == "github":
        return _github_docs(repo)
    if host == "ado":
        return _ado_docs(repo)
    raise GitFetchError(f"Unsupported Git host in URL: {repo.url!r}. Use GitHub or Azure DevOps.")


# ---------------------------------------------------------------- GitHub

def _github_owner_repo(url: str) -> tuple[str, str]:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) < 2:
        raise GitFetchError(f"Cannot parse GitHub owner/repo from {url!r}.")
    return parts[0], parts[1].removesuffix(".git")


def _github_headers(token: str) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_docs(repo: GitRepo) -> list[tuple[str, bytes]]:
    owner, name = _github_owner_repo(repo.url)
    headers = _github_headers(repo.token)
    branch = repo.branch or _github_default_branch(owner, name, headers)
    tree_url = f"https://api.github.com/repos/{owner}/{name}/git/trees/{quote(branch)}?recursive=1"
    resp = _get(tree_url, headers=headers)
    paths = [
        node["path"]
        for node in resp.json().get("tree", [])
        if node.get("type") == "blob" and _is_doc(node.get("path", ""))
    ][:_MAX_FILES]

    out: list[tuple[str, bytes]] = []
    for path in paths:
        content_url = f"https://api.github.com/repos/{owner}/{name}/contents/{quote(path)}?ref={quote(branch)}"
        try:
            body = _get(content_url, headers=headers).json()
        except GitFetchError:
            continue
        data = base64.b64decode(body.get("content", "")) if body.get("encoding") == "base64" else b""
        if 0 < len(data) <= _MAX_BYTES:
            out.append((path, data))
    return out


def _github_default_branch(owner: str, name: str, headers: dict[str, str]) -> str:
    resp = _get(f"https://api.github.com/repos/{owner}/{name}", headers=headers)
    return resp.json().get("default_branch", "main")


# ---------------------------------------------------------------- Azure DevOps

def _ado_org_project_repo(url: str) -> tuple[str, str, str]:
    # https://dev.azure.com/{org}/{project}/_git/{repo}
    parts = [p for p in urlparse(url).path.split("/") if p]
    if "_git" not in parts:
        raise GitFetchError(f"Cannot parse Azure DevOps org/project/repo from {url!r}.")
    i = parts.index("_git")
    if i < 2 or i + 1 >= len(parts):
        raise GitFetchError(f"Cannot parse Azure DevOps org/project/repo from {url!r}.")
    return parts[i - 2], parts[i - 1], parts[i + 1].removesuffix(".git")


def _ado_auth(token: str) -> dict[str, str]:
    if not token:
        raise GitFetchError("Azure DevOps needs a personal access token.")
    basic = base64.b64encode(f":{token}".encode()).decode()
    return {"Authorization": f"Basic {basic}"}


def _ado_docs(repo: GitRepo) -> list[tuple[str, bytes]]:
    org, project, name = _ado_org_project_repo(repo.url)
    headers = _ado_auth(repo.token)
    base = f"https://dev.azure.com/{org}/{quote(project)}/_apis/git/repositories/{quote(name)}"
    branch = repo.branch or ""
    version = f"&versionDescriptor.version={quote(branch)}" if branch else ""
    items_url = f"{base}/items?scopePath=/&recursionLevel=Full&api-version=7.0{version}"
    resp = _get(items_url, headers=headers)
    paths = [
        item["path"]
        for item in resp.json().get("value", [])
        if not item.get("isFolder") and _is_doc(item.get("path", ""))
    ][:_MAX_FILES]

    out: list[tuple[str, bytes]] = []
    for path in paths:
        content_url = f"{base}/items?path={quote(path)}&api-version=7.0{version}"
        try:
            data = _get(content_url, headers=headers).content
        except GitFetchError:
            continue
        if 0 < len(data) <= _MAX_BYTES:
            out.append((path.lstrip("/"), data))
    return out


# ---------------------------------------------------------------- transport

def _get(url: str, *, headers: dict[str, str]) -> requests.Response:
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise GitFetchError(f"Could not reach {url}: {exc}") from exc
    if resp.status_code == 401:
        raise GitFetchError("Access denied — check the personal access token.")
    if resp.status_code == 403:
        if resp.headers.get("X-RateLimit-Remaining") == "0":
            raise GitFetchError("Git host rate limit reached — supply a token (higher limit) or retry later.")
        raise GitFetchError("Access denied — check the repo URL and that the token has read access.")
    if resp.status_code == 404:
        raise GitFetchError(
            "Repository or branch not found. If it is private, the token must have "
            "read access to THIS repo (classic PAT: 'repo' scope; fine-grained: select "
            "the repo + Contents: Read)."
        )
    if resp.status_code >= 400:
        raise GitFetchError(f"Git host returned {resp.status_code}.")
    return resp


__all__ = ["GitRepo", "GitFetchError", "fetch_docs", "detect_host"]
