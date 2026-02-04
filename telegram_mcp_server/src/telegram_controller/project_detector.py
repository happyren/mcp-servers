"""Project name detection from directory structure.

Detects project names from various configuration files.
"""

import configparser
import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("telegram_controller.project_detector")


def detect_project_name(directory: Path) -> str:
    """Auto-detect a friendly project name from the directory.
    
    Tries in order:
    1. Git repository name (from remote origin URL)
    2. package.json name field
    3. pyproject.toml name field
    4. go.mod module name
    5. Cargo.toml package name
    6. Fall back to directory name
    
    Args:
        directory: Project directory
        
    Returns:
        Best-effort project name
    """
    # Try git remote origin
    git_config = directory / ".git" / "config"
    if git_config.exists():
        try:
            config = configparser.ConfigParser()
            config.read(git_config)
            
            # Look for remote "origin" url
            for section in config.sections():
                if section.startswith('remote "') and section.endswith('"'):
                    url = config.get(section, "url", fallback=None)
                    if url:
                        name = _extract_repo_name_from_url(url)
                        if name:
                            logger.debug(f"Detected name from git remote: {name}")
                            return name
        except Exception as e:
            logger.debug(f"Failed to parse git config: {e}")
    
    # Try package.json (Node.js)
    package_json = directory / "package.json"
    if package_json.exists():
        try:
            with open(package_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            name = data.get("name")
            if name and isinstance(name, str):
                # Remove scope prefix like @org/name -> name
                if name.startswith("@") and "/" in name:
                    name = name.split("/", 1)[1]
                logger.debug(f"Detected name from package.json: {name}")
                return name
        except Exception as e:
            logger.debug(f"Failed to parse package.json: {e}")
    
    # Try pyproject.toml (Python)
    pyproject = directory / "pyproject.toml"
    if pyproject.exists():
        try:
            # Simple TOML parsing for name field
            content = pyproject.read_text(encoding="utf-8")
            # Look for name = "..." under [project] or [tool.poetry]
            match = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if match:
                name = match.group(1)
                logger.debug(f"Detected name from pyproject.toml: {name}")
                return name
        except Exception as e:
            logger.debug(f"Failed to parse pyproject.toml: {e}")
    
    # Try go.mod (Go)
    go_mod = directory / "go.mod"
    if go_mod.exists():
        try:
            content = go_mod.read_text(encoding="utf-8")
            # module github.com/user/repo
            match = re.search(r'^module\s+(\S+)', content, re.MULTILINE)
            if match:
                module_path = match.group(1)
                # Extract last segment
                name = module_path.rsplit("/", 1)[-1]
                logger.debug(f"Detected name from go.mod: {name}")
                return name
        except Exception as e:
            logger.debug(f"Failed to parse go.mod: {e}")
    
    # Try Cargo.toml (Rust)
    cargo_toml = directory / "Cargo.toml"
    if cargo_toml.exists():
        try:
            content = cargo_toml.read_text(encoding="utf-8")
            match = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if match:
                name = match.group(1)
                logger.debug(f"Detected name from Cargo.toml: {name}")
                return name
        except Exception as e:
            logger.debug(f"Failed to parse Cargo.toml: {e}")
    
    # Fall back to directory name
    return directory.name


def _extract_repo_name_from_url(url: str) -> Optional[str]:
    """Extract repository name from a git remote URL.
    
    Handles:
    - git@github.com:user/repo.git
    - https://github.com/user/repo.git
    - https://github.com/user/repo
    
    Args:
        url: Git remote URL
        
    Returns:
        Repository name or None
    """
    # Remove trailing .git
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    
    # Handle SSH URLs: git@github.com:user/repo
    if url.startswith("git@") and ":" in url:
        path = url.split(":", 1)[1]
        return path.rsplit("/", 1)[-1]
    
    # Handle HTTP/HTTPS URLs
    if "://" in url:
        path = url.split("://", 1)[1]
        return path.rsplit("/", 1)[-1]
    
    return None
