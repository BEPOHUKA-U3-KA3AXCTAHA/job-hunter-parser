"""Negative tests for scripts/lint_arch.py — for every rule the linter
enforces, build a fake project tree that violates it and confirm the
linter actually bites. If any test passes through, the rule is broken.

Each test monkey-patches lint_arch's module-level paths to a tmpdir and
clears the shared `violations` list before running the relevant check.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def lint_arch(tmp_path, monkeypatch):
    """Re-import a fresh copy with paths pointed at tmp_path."""
    if "lint_arch" in sys.modules:
        del sys.modules["lint_arch"]
    mod = importlib.import_module("lint_arch")
    app = tmp_path / "app"
    (app / "modules").mkdir(parents=True)
    (app / "infra" / "db" / "tables").mkdir(parents=True)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "APP", app)
    monkeypatch.setattr(mod, "MODULES", app / "modules")
    monkeypatch.setattr(mod, "INFRA_TABLES", app / "infra" / "db" / "tables")
    mod.violations.clear()
    return mod


def _make_module(modules: Path, name: str) -> Path:
    m = modules / name
    (m / "ports").mkdir(parents=True)
    (m / "adapters").mkdir(parents=True)
    (m / "ports" / "__init__.py").touch()
    (m / "adapters" / "__init__.py").touch()
    return m


# ---------- Rule 3: adapter folder name == port file stem + class inheritance ----------

def test_rule3_catches_adapter_folder_without_matching_port(lint_arch):
    """Adapter folder `widgets/` exists but no `ports/widgets.py` — should fail."""
    m = _make_module(lint_arch.MODULES, "shop")
    (m / "ports" / "orders.py").write_text("class OrdersRepository: ...\n")
    # Adapter folder named 'widgets' — no matching port.
    bad = m / "adapters" / "widgets"
    bad.mkdir()
    (bad / "sqla.py").write_text("class SqlaWidgets(OrdersRepository): ...\n")

    lint_arch.check_rule_3()
    assert any(
        "Rule 3" in msg and "widgets" in msg
        for _, msg in lint_arch.violations
    ), f"Rule 3 missed an adapter folder with no matching port. Violations: {lint_arch.violations}"


def test_rule3_catches_adapter_class_not_inheriting_from_port(lint_arch):
    """Adapter file in correct folder but its class doesn't inherit
    from anything in the matching port — should fail."""
    m = _make_module(lint_arch.MODULES, "shop")
    (m / "ports" / "orders.py").write_text(
        "from typing import Protocol\n"
        "class OrdersRepository(Protocol): ...\n"
    )
    adapters = m / "adapters" / "orders"
    adapters.mkdir()
    # Class subclasses something completely unrelated — not the port.
    (adapters / "sqla.py").write_text(
        "class SqlaOrders(SomethingElse): ...\n"
    )

    lint_arch.check_rule_3()
    assert any(
        "Rule 3" in msg and "SqlaOrders" in msg
        for _, msg in lint_arch.violations
    ), f"Rule 3 missed a class not inheriting from its port. Violations: {lint_arch.violations}"


def test_rule3_passes_when_class_inherits_from_port(lint_arch):
    """Sanity: correct shape should NOT trigger rule 3."""
    m = _make_module(lint_arch.MODULES, "shop")
    (m / "ports" / "orders.py").write_text(
        "from typing import Protocol\n"
        "class OrdersRepository(Protocol): ...\n"
    )
    adapters = m / "adapters" / "orders"
    adapters.mkdir()
    (adapters / "sqla.py").write_text(
        "class SqlaOrders(OrdersRepository): ...\n"
    )

    lint_arch.check_rule_3()
    assert not lint_arch.violations, (
        f"Rule 3 false-positive on a correctly-structured adapter. "
        f"Violations: {lint_arch.violations}"
    )


# ---------- Rule 4: cross-module imports via __init__.py only ----------

def test_rule4_catches_direct_adapter_import_across_modules(lint_arch):
    _make_module(lint_arch.MODULES, "shop")
    other = _make_module(lint_arch.MODULES, "billing")
    # billing service reaches into shop's adapters directly — bad.
    services = other / "services"
    services.mkdir()
    (services / "do_thing.py").write_text(
        "from app.modules.shop.adapters.orders.sqla import SqlaOrders\n"
    )

    lint_arch.check_rule_4()
    assert any(
        "Rule 4" in msg and "shop.adapters" in msg
        for _, msg in lint_arch.violations
    ), f"Rule 4 missed a cross-module adapter import. Violations: {lint_arch.violations}"


def test_rule4_allows_public_package_import_across_modules(lint_arch):
    _make_module(lint_arch.MODULES, "shop")
    other = _make_module(lint_arch.MODULES, "billing")
    services = other / "services"
    services.mkdir()
    (services / "do_thing.py").write_text(
        "from app.modules.shop import OrdersRepository\n"
    )

    lint_arch.check_rule_4()
    assert not lint_arch.violations, (
        f"Rule 4 false-positive on a public-package import. "
        f"Violations: {lint_arch.violations}"
    )


# ---------- Rule 6: ORM tables only in infra/db/tables/ ----------

def test_rule6_catches_tablename_outside_infra_tables(lint_arch):
    m = _make_module(lint_arch.MODULES, "shop")
    (m / "models" / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
    (m / "rogue.py").write_text(
        "class Foo:\n"
        "    __tablename__ = 'foo'\n"
    )

    lint_arch.check_rule_6()
    assert any(
        "Rule 6" in msg and "__tablename__" in msg
        for _, msg in lint_arch.violations
    ), f"Rule 6 missed a __tablename__ outside infra/db/tables. Violations: {lint_arch.violations}"


def test_rule6_catches_base_subclass_outside_infra_tables(lint_arch):
    m = _make_module(lint_arch.MODULES, "shop")
    (m / "rogue.py").write_text("class Foo(Base): ...\n")

    lint_arch.check_rule_6()
    assert any(
        "Rule 6" in msg and "Foo" in msg
        for _, msg in lint_arch.violations
    ), f"Rule 6 missed a Base subclass outside infra/db/tables. Violations: {lint_arch.violations}"


def test_rule6_passes_when_table_lives_in_infra_tables(lint_arch):
    (lint_arch.INFRA_TABLES / "orders.py").write_text(
        "class Foo(Base):\n"
        "    __tablename__ = 'foo'\n"
    )
    lint_arch.check_rule_6()
    assert not lint_arch.violations, (
        f"Rule 6 false-positive on a table inside infra/db/tables. "
        f"Violations: {lint_arch.violations}"
    )


# ---------- Rule 7: session/commit ONLY in *_uow adapters ----------

def test_rule7_catches_session_commit_in_service(lint_arch):
    """Service file calling session.commit() — should fail."""
    m = _make_module(lint_arch.MODULES, "shop")
    services = m / "services"
    services.mkdir()
    (services / "place_order.py").write_text(
        "async def go(session):\n"
        "    await session.commit()\n"
    )

    lint_arch.check_rule_7()
    assert any(
        "Rule 7" in msg
        for _, msg in lint_arch.violations
    ), f"Rule 7 missed session.commit() in a service. Violations: {lint_arch.violations}"


def test_rule7_catches_session_commit_in_non_uow_adapter(lint_arch):
    """Adapter folder NOT named *_uow but doing session.commit() — should fail.
    This is the test the user explicitly asked for: 'name a port without _uow
    and have an adapter inherit from it that does commits'."""
    m = _make_module(lint_arch.MODULES, "shop")
    (m / "ports" / "orders.py").write_text(
        "from typing import Protocol\n"
        "class OrdersRepository(Protocol): ...\n"
    )
    adapters = m / "adapters" / "orders"  # NOT 'orders_uow'
    adapters.mkdir()
    (adapters / "sqla.py").write_text(
        "class SqlaOrders(OrdersRepository):\n"
        "    async def save(self):\n"
        "        await self._session.commit()\n"
    )

    lint_arch.check_rule_7()
    assert any(
        "Rule 7" in msg
        for _, msg in lint_arch.violations
    ), (
        f"Rule 7 missed session.commit() in a non-UoW adapter "
        f"(folder must end with _uow). Violations: {lint_arch.violations}"
    )


def test_rule7_passes_when_commit_is_inside_uow_adapter(lint_arch):
    """Sanity: commits inside *_uow folders are allowed."""
    m = _make_module(lint_arch.MODULES, "shop")
    (m / "ports" / "orders_uow.py").write_text("class OrdersUoW: ...\n")
    uow = m / "adapters" / "orders_uow"
    uow.mkdir()
    (uow / "sqla.py").write_text(
        "class SqlaOrdersUoW(OrdersUoW):\n"
        "    async def commit(self):\n"
        "        await self._session.commit()\n"
    )

    lint_arch.check_rule_7()
    assert not lint_arch.violations, (
        f"Rule 7 false-positive on session.commit() inside _uow adapter. "
        f"Violations: {lint_arch.violations}"
    )


def test_rule7_catches_get_session_maker_outside_uow(lint_arch):
    """Importing get_session_maker outside a UoW adapter — bad."""
    m = _make_module(lint_arch.MODULES, "shop")
    services = m / "services"
    services.mkdir()
    (services / "rogue.py").write_text(
        "from app.infra.db import get_session_maker\n"
        "session = get_session_maker()()\n"
    )

    lint_arch.check_rule_7()
    assert any(
        "Rule 7" in msg
        for _, msg in lint_arch.violations
    ), (
        f"Rule 7 missed get_session_maker in a service. "
        f"Violations: {lint_arch.violations}"
    )
