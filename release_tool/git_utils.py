"""Git and GitHub helper utilities."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
import sys
from pathlib import Path
from typing import List

import urllib.error
import urllib.request
import urllib.parse


class CommandError(RuntimeError):
    def __init__(self, cmd: List[str], code: int, output: str):
        super().__init__(f"Command {' '.join(cmd)} failed with code {code}: {output}")
        self.cmd = cmd
        self.code = code
        self.output = output


def run(
    cmd: List[str],
    cwd: Path | None = None,
    timeout: int | None = None,
    stream: bool = False,
) -> str:
    """Run a command.

    - When stream=True, stream stdout/stderr to the console in real time.
    - timeout is in seconds. None means no timeout.
    Returns collected stdout (best effort; empty string when streaming).
    """
    if stream:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        output_chunks: list[str] = []
        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ''):
                sys.stdout.write(line)
                output_chunks.append(line)
                if proc.poll() is not None:
                    break
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            raise CommandError(cmd, proc.returncode or -1, ''.join(output_chunks) + f"\nTimed out after {timeout}s")
        if proc.returncode != 0:
            raise CommandError(cmd, proc.returncode, ''.join(output_chunks))
        return ''.join(output_chunks)
    else:
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise CommandError(cmd, -1, f"Timed out after {timeout}s")
        if proc.returncode != 0:
            raise CommandError(cmd, proc.returncode, proc.stdout + proc.stderr)
        return proc.stdout.strip()


def require_clean_working_tree(repo: Path, timeout: int | None = None) -> None:
    status = run(["git", "-C", str(repo), "status", "--porcelain"], timeout=timeout)
    if status:
        raise RuntimeError(
            f"Repository {repo} has uncommitted changes. Please commit/stash/clean before continuing."
        )


def cleanup_release_artifacts(repo: Path) -> int:
    """Remove common Maven release plugin artifacts if present (untracked files).

    Returns count of files removed.
    """
    candidates = [
        "release.properties",
        "pom.xml.releaseBackup",
        "pom.xml.next",
        "pom.xml.tag",
        "pom.xml.branch",
        "pom.xml.backup",
    ]
    removed = 0
    for rel in candidates:
        p = repo / rel
        try:
            if p.exists():
                os.remove(p)
                removed += 1
        except OSError:
            pass
    return removed


def add_and_commit(repo: Path, files: list[str], message: str, timeout: int | None = None) -> bool:
    """Stage given files and commit. Returns True if a commit was created, False if nothing to commit."""
    if not files:
        return False
    run(["git", "-C", str(repo), "add", *files], timeout=timeout)
    try:
        run(["git", "-C", str(repo), "commit", "-m", message], timeout=timeout)
        return True
    except CommandError as e:
        if "nothing to commit" in e.output.lower():
            return False
        raise


def clone_or_update_repo(url: str, dest: Path, timeout: int | None = None, skip_pull: bool = False) -> Path:
    if dest.exists():
        if not skip_pull:
            run(["git", "-C", str(dest), "fetch", "origin"], timeout=timeout)
            run(["git", "-C", str(dest), "reset", "--hard", "origin/master"], timeout=timeout)
    else:
        run(["git", "clone", url, str(dest)], timeout=timeout)
    return dest


def checkout_branch(repo: Path, branch: str, base: str = "master", timeout: int | None = None, skip_pull: bool = False) -> None:
    run(["git", "-C", str(repo), "checkout", base], timeout=timeout)
    if not skip_pull:
        run(["git", "-C", str(repo), "pull", "origin", base], timeout=timeout)
    branches = run(["git", "-C", str(repo), "branch", "--list", branch], timeout=timeout)
    if branches:
        run(["git", "-C", str(repo), "checkout", branch], timeout=timeout)
        if not skip_pull:
            remote_ref = run(
                ["git", "-C", str(repo), "ls-remote", "--heads", "origin", branch], timeout=timeout
            )
            if remote_ref:
                run(["git", "-C", str(repo), "reset", "--hard", f"origin/{branch}"], timeout=timeout)
            else:
                run(["git", "-C", str(repo), "reset", "--hard", base], timeout=timeout)
    else:
        run(["git", "-C", str(repo), "checkout", "-b", branch], timeout=timeout)


def sync_branch(repo: Path, branch: str, strategy: str = "rebase", timeout: int | None = None) -> None:
    """Bring the current branch up-to-date with origin/<branch> using the given strategy.

    strategy: "rebase" (default) or "merge"
    """
    run(["git", "-C", str(repo), "fetch", "origin"], timeout=timeout)
    # Only sync if remote branch exists
    remote_ref = run(["git", "-C", str(repo), "ls-remote", "--heads", "origin", branch], timeout=timeout)
    if not remote_ref:
        return
    if strategy == "merge":
        run(["git", "-C", str(repo), "pull", "origin", branch], timeout=timeout)
    else:
        # default to rebase for linear history
        run(["git", "-C", str(repo), "pull", "--rebase", "origin", branch], timeout=timeout)


def current_commit(repo: Path, timeout: int | None = None) -> str:
    return run(["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=timeout)


def mvn_release_prepare(
    repo: Path,
    timeout: int | None = None,
    release_version: str | None = None,
    development_version: str | None = None,
) -> str:
    cmd = [
        "mvn",
        "-B",
        "clean",
        "release:clean",
        "release:prepare",
        "-DtagNameFormat=@{project.version}",
        "-DautoVersionSubmodules=true",
    ]
    if release_version:
        cmd.append(f"-DreleaseVersion={release_version}")
    if development_version:
        cmd.append(f"-DdevelopmentVersion={development_version}")
    return run(cmd, cwd=repo, timeout=timeout, stream=True)


def tag_exists(repo: Path, tag: str, timeout: int | None = None) -> bool:
    """Return True if the given git tag exists locally or on origin."""
    try:
        local = run(["git", "-C", str(repo), "tag", "-l", tag], timeout=timeout)
        if local.strip():
            return True
        remote = run(["git", "-C", str(repo), "ls-remote", "--tags", "origin", tag], timeout=timeout)
        return bool(remote.strip())
    except CommandError:
        return False


@dataclass
class RepoMatch:
    name: str
    ssh_url: str
    clone_path: Path
    packaging: str | None = None


def search_rpm_repos(artifact_id: str, token: str, org: str, workspace: Path, base_api_url: str) -> List[RepoMatch]:
    query = f"org:{org} filename:pom.xml {artifact_id} packaging:rpm"
    url = f"{base_api_url.rstrip('/')}/search/code?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"Authorization": f"token {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as err:
        raise RuntimeError(
            f"GitHub search API failed with status {err.code}. Ensure your PAT has repo/code-search scopes."
        ) from err
    matches: List[RepoMatch] = []
    host = urllib.parse.urlparse(base_api_url).hostname or "github.com"
    for item in data.get("items", []):
        repo = item.get("repository", {})
        name = repo.get("name") or item.get("name") or "unknown-repo"
        # Prefer ssh_url from API if present; otherwise construct from full_name
        ssh_url = repo.get("ssh_url")
        full_name = repo.get("full_name")
        if not ssh_url:
            if not full_name:
                owner = repo.get("owner", {}).get("login", org)
                full_name = f"{owner}/{name}"
            ssh_url = f"git@{host}:{full_name}.git"
        clone_path = workspace / name
        matches.append(RepoMatch(name=name, ssh_url=ssh_url, clone_path=clone_path, packaging="rpm"))
    return matches


def search_consumers_by_packaging(
    artifact_id: str,
    token: str,
    org: str,
    base_api_url: str,
    packaging: str,
) -> List[RepoMatch]:
    """Search code for pom.xml files that contain the artifact_id and match the given packaging (war/rpm)."""
    query = f"org:{org} filename:pom.xml {artifact_id} packaging:{packaging}"
    url = f"{base_api_url.rstrip('/')}/search/code?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"Authorization": f"token {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as err:
        raise RuntimeError(
            f"GitHub search API failed with status {err.code}. Ensure your PAT has repo/code-search scopes."
        ) from err
    results: List[RepoMatch] = []
    host = urllib.parse.urlparse(base_api_url).hostname or "github.com"
    for item in data.get("items", []):
        repo = item.get("repository", {})
        name = repo.get("name") or item.get("name") or "unknown-repo"
        ssh_url = repo.get("ssh_url")
        full_name = repo.get("full_name")
        if not ssh_url:
            if not full_name:
                owner = repo.get("owner", {}).get("login", org)
                full_name = f"{owner}/{name}"
            ssh_url = f"git@{host}:{full_name}.git"
        # use a temp path; caller may not need clones
        results.append(RepoMatch(name=name, ssh_url=ssh_url, clone_path=Path("/dev/null") / name, packaging=packaging))
    return results
