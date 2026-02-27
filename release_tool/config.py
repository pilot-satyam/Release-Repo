"""Config management for release tool."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any, Dict


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "release-tool" / "config.json"


@dataclasses.dataclass
class ToolConfig:
    github_token: str
    github_org: str = "mmf-cps"
    workspace_dir: str = str(Path.home() / "workspace" / "release-tool" )
    base_api_url: str = "https://github.cerner.com/api/v3"

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "ToolConfig":
        if not path.exists():
            raise FileNotFoundError(
                f"Config file not found at {path}. Create it with GitHub token and workspace_dir."
            )
        with path.open() as fh:
            raw: Dict[str, Any] = json.load(fh)
        # Fill defaults for optional keys
        raw.setdefault("github_org", "mmf-cps")
        raw.setdefault("workspace_dir", str(Path.home() / "workspace" / "release-tool"))
        raw.setdefault("base_api_url", "https://github.cerner.com/api/v3")
        missing = [field for field in ("github_token",) if field not in raw]
        if missing:
            raise ValueError(f"Missing keys in config: {missing}")
        return cls(**raw)


def ensure_workspace(path: str) -> Path:
    workspace = Path(path)
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace