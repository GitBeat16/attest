"""Money is integer paise. Never a float.

Every amount in Attest is an int of paise. Rupee floats are converted only at
the display boundary. This is deliberate: reconciliation lives or dies on
sub-rupee drift, and a float pipeline manufactures the very variances the
system is supposed to detect.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def rupees(amount: float | int | str) -> int:
    """Convert a rupee amount to integer paise using banker-safe rounding."""
    return int(
        (Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def to_rupees(paise: int) -> Decimal:
    """Paise -> Decimal rupees, for display and CSV output only."""
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))


def fmt(paise: int) -> str:
    """Indian-format a paise amount for human output: ₹4,12,880.00"""
    neg = paise < 0
    d = to_rupees(abs(paise))
    whole, frac = f"{d:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{'-' if neg else ''}₹{whole}.{frac}"


def pct(paise: int, rate: Decimal | str) -> int:
    """Apply a percentage rate to a paise amount, rounding half-up to paise.

    Used for MDR and GST. The rounding rule matters: it is the source of the
    legitimate sub-rupee drift the matcher must tolerate, and of the illegitimate
    drift the adversarial auditor must catch.
    """
    return int(
        (Decimal(paise) * Decimal(str(rate)) / 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
