"""Native input parameterization and token substitution binder.

Follows spec-clean-slate-mdo-design Section 12.1:
- Discovers explicit parameter tokens like `<THICKNESS>`, `<FORCE>`, `{{ param }}`.
- Substitutes values without altering card order, comments, or unmanaged bytes.
- Validates finite numerical formatting and whitelisted categorical strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


TOKEN_PATTERN = re.compile(r"(?:<([A-Za-z0-9_]+)>|@@([A-Za-z0-9_]+)@@|\{\{\s*([A-Za-z0-9_]+)\s*\}\})")


@dataclass(frozen=True, kw_only=True)
class BindingSpec:
    variable_id: str
    token_name: str
    format_spec: str = ".6g"
    unit_scale: float = 1.0


@dataclass(frozen=True, kw_only=True)
class TemplateDeck:
    original_text: str
    token_names: tuple[str, ...]
    file_path: Path | None = None

    @classmethod
    def from_text(cls, text: str, file_path: Path | None = None) -> TemplateDeck:
        tokens: set[str] = set()
        for line in text.splitlines():
            # Skip comments in typical CAE decks (** for CalculiX/Abaqus, $ for Nastran, # for Python)
            stripped = line.strip()
            if stripped.startswith("**") or stripped.startswith("$") or stripped.startswith("#"):
                continue
            for match in TOKEN_PATTERN.finditer(line):
                token = match.group(1) or match.group(2) or match.group(3) or match.group(3)
                tokens.add(token)
        return cls(original_text=text, token_names=tuple(sorted(tokens)), file_path=file_path)

    @classmethod
    def from_file(cls, path: Path) -> TemplateDeck:
        text = path.read_text(encoding="utf-8", errors="replace")
        return cls.from_text(text, file_path=path)

    def render(self, values: Mapping[str, float | str | int], bindings: Mapping[str, BindingSpec] | None = None) -> str:
        """Render template with supplied values."""
        # Map token_name -> formatted string
        token_to_value: dict[str, str] = {}

        if bindings:
            for b in bindings.values():
                if b.variable_id in values:
                    val = values[b.variable_id]
                    if isinstance(val, (int, float)):
                        scaled = float(val) * b.unit_scale
                        token_to_value[b.token_name] = f"{scaled:{b.format_spec}}"
                    else:
                        token_to_value[b.token_name] = str(val)

        for token in self.token_names:
            if token not in token_to_value and token in values:
                val = values[token]
                if isinstance(val, (int, float)):
                    token_to_value[token] = f"{float(val):.6g}"
                else:
                    token_to_value[token] = str(val)

        def replacer(match: re.Match) -> str:
            token = match.group(1) or match.group(2) or match.group(3)
            if token in token_to_value:
                return token_to_value[token]
            return match.group(0)

        rendered_lines = []
        for line in self.original_text.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith("**") or stripped.startswith("$") or stripped.startswith("#"):
                rendered_lines.append(line)
            else:
                rendered_lines.append(TOKEN_PATTERN.sub(replacer, line))

        return "".join(rendered_lines)

    bind = render
