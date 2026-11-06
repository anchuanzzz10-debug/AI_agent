"""Project registry: one YAML file per project in config/projects/.

Adding a project = adding a file. No code changes are needed to scale.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .models import ConfigError, ProjectConfig


def config_dir(path: str | os.PathLike | None = None) -> Path:
    root = Path(path or os.environ.get("GUARDIAN_CONFIG", "config/projects")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


class Registry:
    def __init__(self, directory: Path):
        self.directory = directory

    def _file(self, project_id: str) -> Path:
        return self.directory / f"{project_id}.yaml"

    def load_all(self) -> list[ProjectConfig]:
        projects: list[ProjectConfig] = []
        seen: set[str] = set()
        for path in sorted(self.directory.glob("*.yaml")):
            if path.name.endswith(".example.yaml"):
                continue
            project = self._load_file(path)
            if project.id in seen:
                raise ConfigError(f"Duplicate project id {project.id!r}")
            if path.stem != project.id:
                raise ConfigError(f"{path.name}: file name must match project id {project.id!r}")
            seen.add(project.id)
            projects.append(project)
        return projects

    @staticmethod
    def _load_file(path: Path) -> ProjectConfig:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        try:
            return ProjectConfig.from_dict(data)
        except (TypeError, ConfigError) as exc:
            raise ConfigError(f"{path.name}: {exc}") from exc

    def get(self, project_id: str) -> ProjectConfig:
        path = self._file(project_id)
        if not path.exists():
            raise ConfigError(f"No project {project_id!r}")
        return self._load_file(path)

    def save(self, project: ProjectConfig) -> None:
        self._file(project.id).write_text(
            yaml.safe_dump(project.to_dict(), sort_keys=False, allow_unicode=True),
            encoding="utf-8")

    def add(self, project: ProjectConfig) -> None:
        if self._file(project.id).exists():
            raise ConfigError(f"Project {project.id!r} already exists")
        self.save(project)

    def set_feature(self, project_id: str, feature: str, value: bool) -> ProjectConfig:
        project = self.get(project_id)
        project.set_feature(feature, value)
        self.save(project)
        return project
