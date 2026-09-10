"""Attest — prove the close, don't just close the books.

`__version__` is hand-maintained and described in CHANGELOG.md. It covers the
ENGINE: the code that decides an answer. The rules that code applies -- the
tolerances, thresholds, claim windows and tier mappings -- carry a separate,
*computed* digest in `attest.ruleset`, because a hand-maintained rule version
drifts the moment somebody changes a threshold and forgets to bump it.

Bump this when a change could alter a close's conclusion. A refactor that
changes no answer does not need a bump; if you are unsure, it does.
"""

__version__ = "0.3.0"
