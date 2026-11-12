"""Prompt templates stored as plain-text files in ``prompts/``.

Keeping prompts out of the code makes them easy to read, review and cite in
the report, and lets anyone tweak a prompt without touching Python. A prompt
file has a system part and a user part::

    ### SYSTEM
    You are an expert ...
    ### USER
    Log: $log
    ...

Placeholders use ``string.Template`` syntax (``$name``), so the literal JSON
braces in the answer-format instructions need no escaping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

# Default location of the prompt files: <repo>/prompts/.
PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

_SECTION = re.compile(r"^### (SYSTEM|USER)\s*$", re.MULTILINE)


# A loaded prompt with its system and user templates.
@dataclass(frozen=True)
class PromptTemplate:
    name: str
    system: str
    user: str

    # Fill in the placeholders and return chat messages. A missing value
    # raises KeyError, so a prompt can never be sent half-filled.
    def render(self, **values: str) -> list[dict[str, str]]:
        messages = []
        if self.system:
            messages.append({"role": "system", "content": Template(self.system).substitute(values)})
        messages.append({"role": "user", "content": Template(self.user).substitute(values)})
        return messages


# Split a prompt file's text into its ### SYSTEM and ### USER sections.
def parse_prompt(name: str, text: str) -> PromptTemplate:
    parts = _SECTION.split(text)
    # parts = [preamble, "SYSTEM", body, "USER", body] (SYSTEM is optional)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if "USER" not in sections:
        raise ValueError(f"Prompt {name!r} has no '### USER' section")
    return PromptTemplate(name=name, system=sections.get("SYSTEM", ""), user=sections["USER"])


# Load prompts/<name>.txt (or from another directory).
def load_prompt(name: str, directory: str | Path = PROMPTS_DIR) -> PromptTemplate:
    path = Path(directory) / f"{name}.txt"
    return parse_prompt(name, path.read_text(encoding="utf-8"))
