"""Canonical locator for the two CURRENT production input sources.

This module owns exactly two things:

1. the shared fail-closed lexical checkout-root derivation algorithm, used by
   MMI's trusted production module locator to resolve its own checkout root;
2. the canonical repository-relative path components of the current strategy
   settings and portfolio snapshot sources.

It deliberately owns nothing else.  It opens no descriptor, reads no byte,
computes no hash, verifies no expected digest, and knows nothing about MMI
roles, records, provenance seals, diagnostics, or result categories.  Every
secure-filesystem behaviour (``openat2``, no-symlink traversal, bounded reads,
raw SHA-256, expected-hash comparison, record and schema validation, final
bound verification) remains owned by ``investment_orchestrator.mmi``.

This module does not itself provide a second production root call path: it
does not derive a root from its own location, and it exposes no root-yielding
public function.  MMI production capture supplies its own trusted import-time
module locator and module-suffix to the private lexical-root helper below;
private hermetic test seams may do likewise with a controlled locator.  There
is no supported production API by which a caller can select an alternate
checkout, an archived prospective case root, or an arbitrary fixture root.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final


STRATEGY_SETTINGS_PATH_COMPONENTS: Final = (
    "inputs",
    "current",
    "strategy_settings.yaml",
)
PORTFOLIO_SNAPSHOT_PATH_COMPONENTS: Final = (
    "inputs",
    "current",
    "portfolio_snapshot.txt",
)
LONG_HORIZON_RESEARCH_PATH_COMPONENTS: Final = (
    "inputs",
    "current",
    "long_horizon_research.json",
)


class ProductionCheckoutLayoutError(RuntimeError):
    """The executing module is not inside a supported production checkout."""


def _lexical_checkout_root(
    module_file: str | Path,
    *,
    module_path_components: tuple[str, ...],
) -> tuple[Path, Path]:
    """Strip ``module_path_components`` from ``module_file``, fail-closed.

    Returns the checkout root and the normalized module path.  The module path
    must be an absolute, already-normalized, NUL-free string whose trailing
    components are exactly ``module_path_components``; anything else is
    rejected rather than repaired, so an installed or relocated layout can
    never be mistaken for a production checkout.
    """
    try:
        raw = os.fspath(module_file)
    except TypeError:
        raise ProductionCheckoutLayoutError from None
    if (
        type(raw) is not str
        or not os.path.isabs(raw)
        or "\x00" in raw
        or os.path.normpath(raw) != raw
    ):
        raise ProductionCheckoutLayoutError
    module_path = Path(raw)
    if tuple(module_path.parts[-len(module_path_components) :]) != (
        module_path_components
    ):
        raise ProductionCheckoutLayoutError
    checkout_root = module_path
    for _component in module_path_components:
        checkout_root = checkout_root.parent
    if not checkout_root.is_absolute():
        raise ProductionCheckoutLayoutError
    return checkout_root, module_path
