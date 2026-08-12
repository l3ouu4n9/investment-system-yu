"""Foreground CLI that consumes one report-only H1 replacement handoff.

The operator supplies only the expected prepared-handoff identity.  The raw
response is read from the code-owned
``artifacts/current/h1_replacement/h1_response.raw`` leaf that the operator
placed by hand; there is deliberately no response-path argument, no provider or
model option, no network option, and no availability activation option.

Consumption destroys the previous H1 mapping completion before it reads
anything, so this CLI first clears the current Step 1 availability claim that
completion justified, and only a consume that fully succeeds refreshes
availability — with the exact validated facts object the engine returned, in
memory.  A failed consume therefore leaves no H1 claim behind and never restores
the old one.  The recognized H1 state remains strictly non-actionable.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

from investment_orchestrator.workflow.h1_replacement_handoff import (
    H1ReplacementHandoffError,
    consume_h1_replacement_handoff,
)
from investment_orchestrator.workflow.step1_research import (
    refresh_research_availability_for_h1_replacement,
)


AVAILABILITY_LIFECYCLE_EXIT_CODE = 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=(
            "python -m "
            "investment_orchestrator.cli.run_h1_replacement_consume"
        ),
        description=(
            "Consume one prepared H1 replacement handoff and its manually "
            "placed raw response, publishing only the H1 mapping report."
        ),
    )
    parser.add_argument(
        "--expected-prepared-handoff-identity-sha256", required=True
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Consume one prepared handoff and print only stable identities."""
    args = _parser().parse_args(argv)

    # Clear before the engine can invalidate the old mapping completion. On
    # failure nothing has been invalidated, so the surviving availability claim
    # still matches a surviving completion; the engine must not be entered.
    try:
        cleared = refresh_research_availability_for_h1_replacement()
    except Exception as exc:  # noqa: BLE001 - fail closed before any invalidation
        sys.stderr.write(
            "H1_AVAILABILITY_CLEAR_FAILED stage=consume "
            f"error={type(exc).__name__}: {' '.join(str(exc).split())}\n"
        )
        return AVAILABILITY_LIFECYCLE_EXIT_CODE
    sys.stderr.write(
        "research_availability_state_after_clear="
        f"{cleared['research_availability_state']}\n"
    )

    try:
        result = consume_h1_replacement_handoff(
            expected_prepared_handoff_identity_sha256=(
                args.expected_prepared_handoff_identity_sha256
            ),
        )
    except H1ReplacementHandoffError as exc:
        # The old H1 availability claim is already gone and is deliberately not
        # restored: nothing rereads the mapping report or rebuilds old facts.
        sys.stderr.write(
            f"H1_HANDOFF_CONSUME_FAILED code={exc.code.value} "
            f"owner_reason_codes={','.join(exc.owner_reason_codes)}\n"
        )
        return 3

    # The consume completed and published its mapping report, so the exact
    # validated facts object it returned — not a copy, and never a value read
    # back from disk — becomes this run's availability evidence.
    identities = (
        f"workflow_status={result.workflow_status}\n"
        "prepared_handoff_identity_sha256="
        f"{result.prepared_handoff_identity_sha256}\n"
        "mapping_report_identity_sha256="
        f"{result.mapping_report_identity_sha256}\n"
        "portfolio_snapshot_presence="
        f"{result.portfolio_snapshot_presence}\n"
    )
    try:
        refreshed = refresh_research_availability_for_h1_replacement(
            h1_mapped_facts=result.mapped_recognition_facts,
        )
    except Exception as exc:  # noqa: BLE001 - a failed refresh grants nothing
        # The completion is real, so its identities are still reported; the
        # availability artifacts stay absent, which fails closed downstream.
        sys.stdout.write(identities)
        sys.stderr.write(
            "H1_AVAILABILITY_REFRESH_FAILED stage=consume "
            f"error={type(exc).__name__}: {' '.join(str(exc).split())}\n"
        )
        return AVAILABILITY_LIFECYCLE_EXIT_CODE

    sys.stdout.write(identities)
    sys.stderr.write(
        "research_availability_state="
        f"{refreshed['research_availability_state']}\n"
        "research_availability_decision_present="
        f"{refreshed['research_availability_decision_present']}\n"
        f"h1_mapped_selected={refreshed['h1_mapped_selected']}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
