"""Custom architectural linter for rules that import-linter / ruff don't cover.

Rules:
  3. Adapter folder name == port file stem.
     Every adapter file under `app/modules/<m>/adapters/<port>/<impl>.py`
     must pair with a port file `app/modules/<m>/ports/<port>.py`.
  6. DB tables live ONLY in `app/infra/db/tables/*.py`.
     Anything outside that folder defining a SQLA `__tablename__` or
     subclassing `Base` is a violation.
  7. Sessions, commits, rollbacks ONLY in UoW adapters.
     `get_session_maker()`, `Session()`, `session.commit()`,
     `session.rollback()`, `session.close()`, `transaction()` may appear
     ONLY in files matching `app/modules/*/adapters/*_uow/*.py`.
     Repository adapters MUST take a session in __init__ from the UoW.

Usage: .venv/bin/python scripts/lint_arch.py
Exits non-zero on any violation; pre-commit hook fails the commit.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
MODULES = APP / "modules"
INFRA_TABLES = APP / "infra" / "db" / "tables"

violations: list[tuple[str, str]] = []  # (path, message)


def err(path: Path, msg: str) -> None:
    violations.append((str(path.relative_to(ROOT)), msg))


# ---------- Rule 3: adapter folder = port name ----------

def check_rule_3() -> None:
    """Adapter folder name == port file stem, AND every class defined in
    an adapter file must inherit from a class declared in the matching
    port file. Catches both:
      a) adapters/<X>/ with no matching ports/<X>.py
      b) adapters/<X>/<impl>.py defining a class that doesn't inherit
         from any Protocol from ports/<X>.py (i.e., the file is in the
         wrong adapter folder).
    """
    for module_dir in MODULES.iterdir():
        if not module_dir.is_dir():
            continue
        adapters = module_dir / "adapters"
        ports = module_dir / "ports"
        if not adapters.is_dir() or not ports.is_dir():
            continue
        port_files: dict[str, set[str]] = {}
        for p in ports.glob("*.py"):
            if p.name == "__init__.py":
                continue
            try:
                tree = ast.parse(p.read_text())
            except SyntaxError:
                continue
            port_files[p.stem] = {
                node.name for node in tree.body if isinstance(node, ast.ClassDef)
            }
        port_stems = set(port_files)

        for adapter_subdir in adapters.iterdir():
            if not adapter_subdir.is_dir() or adapter_subdir.name == "__pycache__":
                continue
            stem = adapter_subdir.name
            # 3a — folder must have a matching port.
            if stem not in port_stems:
                err(
                    adapter_subdir,
                    f"Rule 3: adapter folder '{stem}' has no matching port "
                    f"file '{module_dir.name}/ports/{stem}.py'",
                )
                continue
            allowed_bases = set(port_files[stem])
            # Also accept inheritance from any class defined in a sibling
            # file inside the same adapter folder — e.g. an `llm/base.py`
            # that implements the port and is then extended by
            # `llm/anthropic.py`. Walk all sibling files to collect
            # those names too.
            sibling_classes: set[str] = set()
            for f in adapter_subdir.glob("*.py"):
                if f.name == "__init__.py":
                    continue
                try:
                    tree = ast.parse(f.read_text())
                except SyntaxError:
                    continue
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        sibling_classes.add(node.name)
            allowed_bases |= sibling_classes

            # 3b — every NON-DTO class in each adapter file must inherit
            #      transitively from a port Protocol. Classes with no base
            #      at all (dataclasses, NamedTuples, utility/result types)
            #      are exempt — they're DTOs, not adapters.
            for f in adapter_subdir.glob("*.py"):
                if f.name == "__init__.py":
                    continue
                try:
                    tree = ast.parse(f.read_text())
                except SyntaxError:
                    continue
                for node in tree.body:
                    if not isinstance(node, ast.ClassDef):
                        continue
                    if node.name.startswith("_"):
                        continue
                    base_names = []
                    for b in node.bases:
                        if isinstance(b, ast.Name):
                            base_names.append(b.id)
                        elif isinstance(b, ast.Attribute):
                            base_names.append(b.attr)
                    if not base_names:
                        # Pure DTO / utility (no inheritance) — fine.
                        continue
                    if not any(b in allowed_bases for b in base_names):
                        err(
                            f,
                            f"Rule 3: class {node.name} inherits from "
                            f"{base_names} but adapter folder '{stem}' "
                            f"requires a base from ports/{stem}.py "
                            f"({sorted(port_files[stem])}) or a sibling "
                            f"file in the same folder — move this file "
                            f"to the correct adapters/<port>/ folder",
                        )


# ---------- Rule 6: ORM tables only in infra/db/tables/ ----------

def check_rule_6() -> None:
    """Find any `__tablename__` assignment or `Base` subclass outside
    app/infra/db/tables/."""
    for py in APP.rglob("*.py"):
        if INFRA_TABLES in py.parents:
            continue
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "__tablename__":
                        err(py, f"Rule 6: __tablename__ assignment outside infra/db/tables/")
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    name = (
                        base.id if isinstance(base, ast.Name)
                        else (base.attr if isinstance(base, ast.Attribute) else None)
                    )
                    if name == "Base":
                        err(py, f"Rule 6: class {node.name} subclasses Base outside infra/db/tables/")


# ---------- Rule 7: sessions/commits ONLY in *_uow adapters ----------

# Matched substrings — if any appear in a file's source they trigger the rule
# unless the file is in an allow-listed location.
SESSION_PATTERNS = [
    # Importing/calling the session factory from infra is bad outside UoW.
    re.compile(r"\bget_session_maker\b"),
    re.compile(r"from app\.infra\.db import .*\btransaction\b"),
    re.compile(r"\btransaction\(\)\b.*as session"),
    # Mutating a SQLA session directly (only the UoW should).
    re.compile(r"\bsession\.commit\(\)"),
    re.compile(r"\bsession\.rollback\(\)"),
    re.compile(r"\bsession\.close\(\)"),
    re.compile(r"\b_session\.commit\(\)"),
    re.compile(r"\b_session\.rollback\(\)"),
    re.compile(r"\b_session\.close\(\)"),
    re.compile(r"\bself\._s\.commit\(\)"),
    re.compile(r"\bself\._s\.rollback\(\)"),
    # Opening a session by hand.
    re.compile(r"async with Session\(\)"),
    re.compile(r"\bAsyncSession\(\s*[a-zA-Z_]"),  # AsyncSession(engine, ...) — direct construction
]

# Adapter folders that are UoW: stem ends in `_uow`.
def _is_uow_file(path: Path) -> bool:
    parts = path.parts
    if "adapters" not in parts:
        return False
    i = parts.index("adapters")
    if i + 1 >= len(parts):
        return False
    return parts[i + 1].endswith("_uow")


def _is_infra_file(path: Path) -> bool:
    return APP / "infra" in path.parents or path.is_relative_to(APP / "infra")


def check_rule_4() -> None:
    """Cross-module imports go through __init__.py only.

    Direct source statements like `from app.modules.<other>.adapters.X
    import Y` are violations. Importing the public package
    `from app.modules.<other> import Y` is fine — even if that other
    module's __init__ internally pulls in its own subpackages.

    `app/entrypoints/` is a composition root and is allowed to import
    concrete adapters by name (per the README's wiring convention).
    """
    PRIVATE_SUBPACKAGES = {"adapters", "services", "models", "ports"}
    for py in APP.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(APP)
        # Composition root — allowed to import any adapter directly.
        if rel.parts and rel.parts[0] == "entrypoints":
            continue
        own_module: str | None = None
        if rel.parts and rel.parts[0] == "modules" and len(rel.parts) >= 2:
            own_module = rel.parts[1]
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            mod = (
                node.module if isinstance(node, ast.ImportFrom)
                else node.names[0].name if isinstance(node, ast.Import)
                else None
            )
            if not mod or not mod.startswith("app.modules."):
                continue
            parts = mod.split(".")
            if len(parts) < 4:
                continue  # `app.modules.<name>` — public package, fine
            target_module = parts[2]
            target_sub = parts[3]
            if target_sub not in PRIVATE_SUBPACKAGES:
                continue
            if target_module == own_module:
                continue  # in-module import — fine
            err(
                py,
                f"Rule 4: imports into another module's internals "
                f"`{mod}` (use `from app.modules.{target_module} import ...` "
                f"via the public __init__.py instead)",
            )


def check_rule_7() -> None:
    """Session lifecycle calls only in *_uow adapter files (or infra/db
    itself, where the helpers are defined)."""
    for py in APP.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if py.is_relative_to(APP / "infra"):
            continue  # infra defines these primitives
        if _is_uow_file(py):
            continue  # legitimate location
        text = py.read_text()
        for pat in SESSION_PATTERNS:
            if pat.search(text):
                err(
                    py,
                    f"Rule 7: session/commit primitive {pat.pattern!r} found outside "
                    f"`adapters/*_uow/` — move into the UoW adapter",
                )
                break


# ---------- main ----------

def main() -> int:
    check_rule_3()
    check_rule_4()
    check_rule_6()
    check_rule_7()
    if not violations:
        print("✓ lint_arch: 0 violations")
        return 0
    print(f"✗ lint_arch: {len(violations)} violation(s):\n")
    for path, msg in violations:
        print(f"  {path}: {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
