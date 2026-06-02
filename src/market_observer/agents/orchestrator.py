"""Deterministic orchestrator: a fixed DAG, not an LLM router.

    data ──► technical ─┐
         ──► options   ─┼─► synthesizer ─► Briefing
         ──► macro      ─┘

The order is fixed in code; no LLM decides the next step. The workflow always
terminates (fixed number of steps, no loops). A specialist failure does not
abort the pipeline — its output is marked not-ok and the briefing still renders
from the data plus whatever succeeded. If the synthesizer fails, the briefing
still carries the pure data and the specialist notes.
"""

from __future__ import annotations

import logging
from datetime import datetime

from market_observer.domain.models import Briefing, BriefingData

from . import macro_agent, options_agent, synthesizer, technical_agent
from .llm_client import LLMClient

logger = logging.getLogger(__name__)


def run_briefing(
    llm: LLMClient,
    data: BriefingData,
    now: datetime | None = None,
) -> Briefing:
    """Execute the fixed DAG and assemble the Briefing."""
    generated_at = now or datetime.now()

    # Step 1: three independent specialists (fixed order).
    technical = technical_agent.run(llm, data)
    options = options_agent.run(llm, data)
    macro = macro_agent.run(llm, data)

    # Step 2: synthesizer depends on all three.
    synthesis = synthesizer.run(llm, data, technical, options, macro)

    return Briefing(
        generated_at=generated_at,
        data=data,
        technical=technical,
        options=options,
        macro_analysis=macro,
        synthesis=synthesis,
    )
