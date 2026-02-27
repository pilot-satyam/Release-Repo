"""Utilities for reading and updating Maven POM files."""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple, Optional

from lxml import etree


def _parse_pom(pom_path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=True)
    return etree.parse(str(pom_path), parser)


def update_dependency_version(
    pom_path: Path,
    artifact_id: str,
    new_version: str,
) -> bool:
    tree = _parse_pom(pom_path)
    ns = {"m": tree.getroot().nsmap.get(None)}
    updated = False
    for dep in tree.xpath("//m:dependency[m:artifactId = $artifact]", artifact=artifact_id, namespaces=ns):
        version_node = dep.find("m:version", namespaces=ns)
        if version_node is not None:
            version_node.text = new_version
            updated = True
    if updated:
        tree.write(str(pom_path), pretty_print=True, xml_declaration=True, encoding="UTF-8")
    return updated


def has_snapshot_versions(pom_path: Path) -> bool:
    tree = _parse_pom(pom_path)
    text = etree.tostring(tree.getroot()).decode()
    return "-SNAPSHOT" in text


def list_snapshot_dependencies(pom_path: Path) -> List[Tuple[str, str, str]]:
    """Return list of (groupId, artifactId, version) where version contains -SNAPSHOT.

    This scans dependencyManagement and dependencies sections.
    """
    tree = _parse_pom(pom_path)
    ns = {"m": tree.getroot().nsmap.get(None)}
    results: List[Tuple[str, str, str]] = []
    for dep in tree.xpath("//m:dependency", namespaces=ns):
        gid = dep.findtext("m:groupId", namespaces=ns) or ""
        aid = dep.findtext("m:artifactId", namespaces=ns) or ""
        ver = dep.findtext("m:version", namespaces=ns) or ""
        if ver and "-SNAPSHOT" in ver:
            results.append((gid, aid, ver))
    return results


def drop_snapshot_versions(pom_path: Path) -> int:
    """Replace any dependency version that ends with -SNAPSHOT by its release (strip suffix).

    Returns the number of dependencies updated.
    """
    tree = _parse_pom(pom_path)
    ns = {"m": tree.getroot().nsmap.get(None)}
    changed = 0
    for dep in tree.xpath("//m:dependency", namespaces=ns):
        version_node = dep.find("m:version", namespaces=ns)
        if version_node is not None and version_node.text and version_node.text.endswith("-SNAPSHOT"):
            version_node.text = version_node.text.replace("-SNAPSHOT", "")
            changed += 1
    if changed:
        tree.write(str(pom_path), pretty_print=True, xml_declaration=True, encoding="UTF-8")
    return changed


def has_dependency_snapshots(pom_path: Path) -> bool:
    """Return True if any <dependency><version> contains -SNAPSHOT (ignores project <version>)."""
    tree = _parse_pom(pom_path)
    ns = {"m": tree.getroot().nsmap.get(None)}
    for dep in tree.xpath("//m:dependency", namespaces=ns):
        version_node = dep.find("m:version", namespaces=ns)
        if version_node is not None and version_node.text and "-SNAPSHOT" in version_node.text:
            return True
    return False


def get_project_version(pom_path: Path) -> Optional[str]:
    """Return the project's <version> from the root POM, or None if not found."""
    tree = _parse_pom(pom_path)
    ns = {"m": tree.getroot().nsmap.get(None)}
    version = tree.findtext("m:version", namespaces=ns)
    if version:
        return version.strip()
    return None
