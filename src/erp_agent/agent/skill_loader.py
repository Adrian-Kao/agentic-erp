import re
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, Field

MAX_SKILL_BYTES = 16_384
SKILL_NAMES = ("fx-exposure-analysis", "erp-data-quality")
SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills"


class SkillError(ValueError):
    """An untrusted or malformed skill was rejected."""


class SkillMetadata(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=1024)


class SkillRegistry:
    def __init__(self, root: Path = SKILL_ROOT, allowed: tuple[str, ...] = SKILL_NAMES) -> None:
        self.root = root.resolve()
        self._skills: dict[str, tuple[SkillMetadata, Path]] = {}
        for folder in allowed:
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", folder):
                raise SkillError("Invalid skill folder name")
            path = self._safe_path(self.root, folder, "SKILL.md")
            metadata, _ = self._parse(path)
            if metadata.name in self._skills:
                raise SkillError("Duplicate skill name")
            if metadata.name != folder:
                raise SkillError("Skill name must match the folder")
            self._skills[metadata.name] = (metadata, path)

    @staticmethod
    def _safe_path(root: Path, *parts: str) -> Path:
        path = root.joinpath(*parts)
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or resolved != path:
            raise SkillError("Skill path escaped its trusted location or used a symlink")
        return resolved

    @staticmethod
    def _read(path: Path) -> str:
        try:
            with path.open("rb") as handle:
                data = handle.read(MAX_SKILL_BYTES + 1)
            if len(data) > MAX_SKILL_BYTES:
                raise SkillError("Skill file exceeds its size limit")
            return data.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise SkillError("Skill is unavailable or is not UTF-8") from exc

    @classmethod
    def _parse(cls, path: Path) -> tuple[SkillMetadata, str]:
        content = cls._read(path).replace("\r\n", "\n")
        if not content.startswith("---\n"):
            raise SkillError("Skill is missing YAML frontmatter")
        header, end, body = content[4:].partition("\n---\n")
        if not end or not body.strip():
            raise SkillError("Skill is missing frontmatter or instructions")
        try:
            if any(
                isinstance(token, (yaml.AnchorToken, yaml.AliasToken))
                for token in yaml.scan(header)
            ):
                raise SkillError("Skill frontmatter cannot use YAML anchors")
            # Read only the two public metadata fields.
            parsed = yaml.safe_load(header)
            metadata = SkillMetadata.model_validate(parsed)
        except (ValueError, yaml.YAMLError) as exc:
            raise SkillError("Invalid skill metadata") from exc
        return metadata, body.strip()

    def list(self) -> list[SkillMetadata]:
        return [metadata.model_copy() for metadata, _ in self._skills.values()]

    def load(self, name: str) -> str:
        if name not in self._skills:
            raise SkillError("Unknown skill")
        metadata, path = self._skills[name]
        self._safe_path(self.root, name, "SKILL.md")
        refreshed, body = self._parse(path)
        if refreshed != metadata:
            raise SkillError("Skill metadata changed since startup")
        return body

    def reference(self, name: str, relative_path: str) -> str:
        path = PurePosixPath(relative_path)
        parts = relative_path.split("/")
        if (
            name not in self._skills
            or len(parts) < 2
            or parts[0] != "references"
            or path.suffix != ".md"
            or any(not re.fullmatch(r"[a-zA-Z0-9_-]+(?:\.md)?", part) for part in parts)
        ):
            raise SkillError("Unapproved reference path")
        return self._read(self._safe_path(self.root, name, *parts))

    @staticmethod
    def select(message: str) -> tuple[str, ...]:
        if re.search(
            r"quality|overdue|missing|invalid|異常|品質|逾期|缺少|缺漏", message, re.IGNORECASE
        ):
            return SKILL_NAMES
        if re.search(r"schema|fields?|entity list|法人清單|哪些法人|欄位", message, re.IGNORECASE):
            return ()
        return (SKILL_NAMES[0],)
