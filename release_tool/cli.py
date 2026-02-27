"""Entry point for release tool workflow."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import ToolConfig, ensure_workspace
from .git_utils import (
    checkout_branch,
    clone_or_update_repo,
    require_clean_working_tree,
    mvn_release_prepare,
    search_rpm_repos,
    cleanup_release_artifacts,
    add_and_commit,
    sync_branch,
    tag_exists,
)
from .pom_editor import has_snapshot_versions, update_dependency_version, list_snapshot_dependencies, drop_snapshot_versions, has_dependency_snapshots, get_project_version
from typing import Optional
import os


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Release automation tool")
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--war-version", required=False, help="Optional. If omitted, the current WAR pom.xml <version> will be used and sanitized (-SNAPSHOT dropped)")
    parser.add_argument("--release", required=True)
    parser.add_argument("--war-path", required=True, help="Path to WAR repo")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--timeout", type=int, default=900, help="Per-command timeout in seconds (default: 900)")
    parser.add_argument("--skip-pull", action="store_true", help="Skip 'git pull' and remote branch resets (use local state)")
    parser.add_argument("--sync-strategy", choices=["rebase", "merge"], default="rebase", help="How to sync with origin before release (default: rebase)")
    parser.add_argument("--rpm-war-version", required=False, help="Override WAR version to set in RPMs (defaults to released scm.tag or --war-version/derived)")
    parser.add_argument("--stop-after-war", action="store_true", help="Run WAR release only and stop before RPM updates")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    logger = logging.getLogger("release_tool")
    config = ToolConfig.load()
    workspace = ensure_workspace(config.workspace_dir)

    war_repo = Path(args.war_path)
    if not war_repo.exists():
        raise FileNotFoundError(f"WAR repo not found at {war_repo}")
    war_branch = f"release-{args.release}"
    logger.info("Preparing WAR repo %s on branch %s", war_repo, war_branch)
    checkout_branch(war_repo, war_branch, timeout=args.timeout, skip_pull=args.skip_pull)
    # Clean up any previous release plugin artifacts and auto-drop -SNAPSHOT versions in WAR POM
    removed = cleanup_release_artifacts(war_repo)
    if removed:
        logger.info("Removed %d prior release artifact files (e.g., release.properties)", removed)
    war_pom = war_repo / "pom.xml"
    # If --war-version not provided, derive from current project version (drop -SNAPSHOT)
    derived_war_version = None
    if not args.war_version:
        pv = get_project_version(war_pom)
        if pv:
            derived_war_version = pv.replace("-SNAPSHOT", "")
            logger.info("Derived WAR version from pom.xml: %s", derived_war_version)
    changed = drop_snapshot_versions(war_pom)
    if changed:
        logger.info("Dropped -SNAPSHOT from %d WAR dependencies", changed)
        add_and_commit(war_repo, [str(war_pom)], "chore(release): drop -SNAPSHOT from WAR dependencies", timeout=args.timeout)
    # Verify no lingering SNAPSHOT deps remain in WAR POM
    war_snapshots = list_snapshot_dependencies(war_pom)
    if war_snapshots:
        logger.error("WAR POM still contains SNAPSHOT dependencies; please release or pin them before proceeding:")
        for gid, aid, ver in war_snapshots:
            logger.error("  - %s:%s:%s", gid, aid, ver)
        return 2
    # Ensure working tree is clean before running the Maven release
    require_clean_working_tree(war_repo, timeout=args.timeout)
    # Ensure local release branch is up to date with remote before running release
    sync_branch(war_repo, war_branch, strategy=args.sync_strategy, timeout=args.timeout)
    # Decide release version to use for WAR (explicitly control the release tag)
    planned_release_version = (args.war_version or derived_war_version or "").replace("-SNAPSHOT", "")
    if not planned_release_version:
        raise RuntimeError("Unable to determine planned WAR release version from --war-version or pom.xml")
    planned_dev_version = _bump_patch_snapshot(planned_release_version)

    # Avoid re-releasing the same tag if it already exists
    if tag_exists(war_repo, planned_release_version, timeout=args.timeout):
        logger.info("Tag %s already exists; skipping WAR release:prepare", planned_release_version)
    else:
        logger.info("Running Maven release prepare for WAR repo with releaseVersion=%s developmentVersion=%s",
                    planned_release_version, planned_dev_version)
        mvn_release_prepare(
            war_repo,
            timeout=args.timeout,
            release_version=planned_release_version,
            development_version=planned_dev_version,
        )

    # Determine the released WAR version from release.properties (scm.tag)
    released_war_version = read_released_version(war_repo) or planned_release_version
    if released_war_version:
        logger.info("Detected released WAR version from scm.tag: %s", released_war_version)
    else:
        logger.warning("Could not detect released WAR version from release.properties; falling back to --war-version")
    if args.stop_after_war:
        logger.info("--stop-after-war specified; skipping RPM updates. You can re-run later to update RPMs.")
        return 0

    rpm_matches = search_rpm_repos(
        artifact_id=args.artifact,
        token=config.github_token,
        org=config.github_org,
        workspace=workspace,
        base_api_url=config.base_api_url,
    )
    if not rpm_matches:
        logger.warning("No RPM repos found consuming artifact %s", args.artifact)
    final_version_source = args.rpm_war_version or released_war_version or args.war_version or derived_war_version or ""
    if not final_version_source:
        raise RuntimeError("Unable to determine WAR version. Provide --war-version or ensure pom.xml has <version>.")
    final_version = final_version_source.replace("-SNAPSHOT", "")
    # Progress tracking
    discovered = [r.name for r in rpm_matches]
    released_repos: list[str] = []
    skipped: list[str] = []
    failed: dict[str, str] = {}
    if discovered:
        logger.info("RPMs to process (%d): %s", len(discovered), ", ".join(discovered))

    for repo in rpm_matches:
        logger.info("Updating RPM repo: %s", repo.name)
        clone_or_update_repo(repo.ssh_url, repo.clone_path, timeout=args.timeout, skip_pull=args.skip_pull)
        rpm_branch = f"release-{args.release}"
        logger.info("Ensuring release branch %s for %s", rpm_branch, repo.name)
        checkout_branch(repo.clone_path, rpm_branch, timeout=args.timeout, skip_pull=args.skip_pull)
        sync_branch(repo.clone_path, rpm_branch, strategy=args.sync_strategy, timeout=args.timeout)
        # Clean previous release artifacts in RPM repo, if any
        removed_rpm = cleanup_release_artifacts(repo.clone_path)
        if removed_rpm:
            logger.info("Removed %d prior release artifact files in %s", removed_rpm, repo.name)
        pom_path = repo.clone_path / "pom.xml"
        logger.debug("Updating %s dependency version to %s", args.artifact, final_version)
        dep_updated = update_dependency_version(pom_path, args.artifact, final_version)
        if not dep_updated:
            logger.warning("%s did not reference %s", repo.name, args.artifact)
        # Commit dependency update so release plugin sees a clean tree
        if dep_updated:
            add_and_commit(repo.clone_path, [str(pom_path)], f"chore: update {args.artifact} to {final_version}", timeout=args.timeout)
        # Auto-drop any remaining -SNAPSHOT dependency versions in RPM POM
        rpm_changed = drop_snapshot_versions(pom_path)
        if rpm_changed:
            logger.info("Dropped -SNAPSHOT from %d dependencies in %s", rpm_changed, repo.name)
            add_and_commit(repo.clone_path, [str(pom_path)], "chore(release): drop -SNAPSHOT from RPM dependencies", timeout=args.timeout)
        # Safety: block if dependency SNAPSHOTs still remain (ignore project version)
        if has_dependency_snapshots(pom_path):
            snaps = list_snapshot_dependencies(pom_path)
            logger.error("%s still has dependency -SNAPSHOTs after update:", repo.name)
            for gid, aid, ver in snaps:
                logger.error("  - %s:%s:%s", gid, aid, ver)
            raise RuntimeError(f"{repo.name} still has -SNAPSHOT versions after update")
        require_clean_working_tree(repo.clone_path, timeout=args.timeout)
        if not confirm_release(repo.name):
            logger.info("Skipping Maven release for %s per user request", repo.name)
            skipped.append(repo.name)
            continue
        logger.info("Running Maven release prepare for %s", repo.name)
        try:
            mvn_release_prepare(repo.clone_path, timeout=args.timeout)
            logger.info("Completed release for %s", repo.name)
            released_repos.append(repo.name)
        except Exception as e:
            logger.error("Release failed for %s: %s", repo.name, e)
            failed[repo.name] = str(e)
            continue
    # Final summary
    print("\n===== Release Summary =====")
    print(f"WAR release branch: release-{args.release} (prepared)")
    print(f"RPMs discovered: {len(discovered)}")
    if released_repos:
        print(f"Updated ({len(released_repos)}): " + ", ".join(released_repos))
    if skipped:
        print(f"Skipped by user ({len(skipped)}): " + ", ".join(skipped))
    if failed:
        print(f"Failed ({len(failed)}): " + ", ".join(f"{k}: {v}" for k, v in failed.items()))
    print("==========================\n")
    return 0


def read_released_version(war_repo: Path) -> Optional[str]:
    """Parse release.properties to extract the released version (scm.tag)."""
    props_path = war_repo / "release.properties"
    if not props_path.exists():
        return None
    version: Optional[str] = None
    try:
        with props_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    if k.strip() == 'scm.tag':
                        version = v.strip()
                        break
    except Exception:
        return None
    return version


def _bump_patch_snapshot(version: str) -> str:
    """Return next patch snapshot version for a semantic version string (e.g., 3.20 -> 3.21-SNAPSHOT).
    Falls back to appending -SNAPSHOT if parsing fails.
    """
    parts = version.split('.')
    try:
        if len(parts) >= 3:
            parts[-1] = str(int(parts[-1]) + 1)
        elif len(parts) == 2:
            parts.append(str(int(parts[-1]) + 1))
        else:
            # single number, bump it
            parts = [str(int(parts[0]) + 1)]
        return '.'.join(parts) + "-SNAPSHOT"
    except ValueError:
        return version + "-SNAPSHOT"


def confirm_release(repo_name: str) -> bool:
    while True:
        response = input(f"Proceed with Maven release for {repo_name}? (yes/no): ").strip().lower()
        if response in {"yes", "y"}:
            return True
        if response in {"no", "n"}:
            return False
        print("Please answer 'yes' or 'no'.")


if __name__ == "__main__":
    raise SystemExit(main())