"""Find and import the project this edge box runs (SFS, BG, ...).

agent.py is the same file in every project repo. The active project is:
  1. env EDGE_PROJECT, else
  2. the only folder in <program>/projects that has a config.py.
"""
import importlib
import os
from typing import List


def available_projects(program_dir: str) -> List[str]:
    base = os.path.join(program_dir, "projects")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    return [n for n in names if os.path.isfile(os.path.join(base, n, "config.py"))]


def active_project_name(program_dir: str) -> str:
    name = (os.environ.get("EDGE_PROJECT") or "").strip()
    found = available_projects(program_dir)
    if name:
        if name not in found:
            raise RuntimeError(f"EDGE_PROJECT={name!r} not found in projects/ ({found})")
        return name
    if len(found) == 1:
        return found[0]
    raise RuntimeError(f"Set EDGE_PROJECT to one of {found}" if found else "No project found in projects/")


def load(program_dir: str, submodule: str, name: str = None):
    """Import projects.<name>.<submodule>."""
    name = name or active_project_name(program_dir)
    return importlib.import_module(f"projects.{name}.{submodule}")
