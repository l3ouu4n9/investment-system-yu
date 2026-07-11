"""Offline, report-only evidence tooling.

Nothing in this package is imported by production runtime modules (Step 1
parsing, research availability, gates, order path, broker/live execution).  It
is invoked only by operators / CI for review.  A static isolation test enforces
the no-runtime-import boundary.
"""
