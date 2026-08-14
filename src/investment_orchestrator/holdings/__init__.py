"""Strict positive ETF holdings-domain contract owner.

This package owns the existing parser-defined strict-holdings domain types
and the fixed current-source accessor.  It has no provider client, no
valuation logic, no session resolver, no publication, no prompt, no gate,
no order authority, and no observation capability.

The types and accessor were extracted verbatim from
``observability.report_only_holdings_exposure`` to eliminate a false
``market → observability`` dependency while preserving byte-identical
parser and provenance behaviour.
"""

__all__ = ()
