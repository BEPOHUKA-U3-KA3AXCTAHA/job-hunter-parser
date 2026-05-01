"""Automation module — public API.

This module orchestrates browser-driven LinkedIn use cases:
- mass_apply via Easy Apply (Selenium + real Firefox profile + Shadow DOM walker)
- dm_outreach via LinkedIn DMs

Cross-module callers MUST import only from here:
    from app.modules.automation import run_batch, run_send_batch, ApplyOutcome
"""
from app.modules.automation.adapters.selenium_bot import ApplyOutcome, ApplyResult
from app.modules.automation.services.selenium_orchestrator import run_batch
from app.modules.automation.services.send_orchestrator import run_send_batch

__all__ = ["ApplyOutcome", "ApplyResult", "run_batch", "run_send_batch"]
