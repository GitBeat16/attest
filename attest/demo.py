"""A deterministic demonstration of a compensating error.

The point of this module is one screen: a settlement batch that agrees with the
bank **to within twenty paise**, and contains **₹8,495.80** of real error. Every
total-level check in existence passes it. That gap — a factor of forty-two
thousand — is the entire argument for the product.

Nothing here is a fixture of *results*. The numbers below are inputs: a set of
source documents a merchant would actually receive. They are then read by the
same loader, matched by the same engine and attacked by the same adversarial
pass as any other month. The findings are discovered, not declared, and
`check()` fails loudly if the engine ever stops finding them.

The scenario, laid out plainly:

    Batch SETL-A   100 payments of ₹20,000, charged at 2.18% MDR
                   against a contracted 2.00%
                   ->  MDR overcharged      ₹3,600.00
                       GST on that overcharge  ₹648.00
                       fee overcharge total  ₹4,248.00   (batch net too LOW)

                   A ₹12,000 refund was due to net into this batch.
                   The report deducted ₹7,752.20.
                   ->  refund under-deducted ₹4,247.80   (batch net too HIGH)

                   net visible variance          ₹0.20
                   actual error inside        ₹8,495.80

    Batch SETL-B   clean, ties, nothing wrong. A demo where everything is
                   broken teaches a judge nothing about false positives.

    Batch SETL-C   settled by Razorpay, never credited by the bank.
                   A plain, large, recoverable exception with a real deadline.

    Courier        one COD remittance short by the RTO freight on a delivered
                   order — a second counterparty, with a 14-day claim window.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta

MERCHANT = "Lakeview Naturals Private Limited"
PERIOD = "2026-08"

# --- the contract, which is what "overcharged" is measured against ----------
MDR_PCT = "2.00"
GST_PCT = "18.00"
COD_FEE_PCT = "1.50"
RTO_FREIGHT = "85.00"

# --- batch A: the compensating pair ----------------------------------------
A_LINES = 100
A_GROSS = 2_000_000            # ₹20,000.00 per payment, in paise
A_MDR_CHARGED = 43_600         # 2.18% -- the contract says 2.00% = 40,000
A_GST_CHARGED = 7_848          # 18% of the charged MDR
A_STATED_REFUND = 775_220      # what the settlement report deducted
A_TRUE_REFUND = 1_200_000      # what the merchant's own refund export shows

A_SETTLED = date(2026, 8, 14)
B_SETTLED = date(2026, 8, 7)
C_SETTLED = date(2026, 8, 21)

# --- batch B: clean -------------------------------------------------------
B_LINES = 40
B_GROSS = 1_500_000            # ₹15,000.00

# --- batch C: settled, never credited -------------------------------------
C_LINES = 18
C_GROSS = 2_450_000            # ₹24,500.00


def _r(paise: int) -> str:
    """Paise to the decimal rupee string the source documents carry."""
    sign = "-" if paise < 0 else ""
    p = abs(int(paise))
    return f"{sign}{p // 100}.{p % 100:02d}"


def _mdr(gross: int) -> int:
    return gross * 200 // 10_000            # the contracted 2.00%


def _gst(mdr: int) -> int:
    return mdr * 1_800 // 10_000            # 18% on the fee


def _lines(prefix: str, n: int, gross: int, mdr: int, gst: int, start: int):
    for i in range(n):
        yield {
            "payment_id": f"pay_{prefix}{start + i:06d}",
            "order_id": f"ORD{prefix}{start + i:06d}",
            "gross": gross, "mdr": mdr, "gst": gst,
            "net": gross - mdr - gst,
        }


def build() -> dict[str, str]:
    """Return every source document as text, keyed by filename."""
    a = list(_lines("A", A_LINES, A_GROSS, A_MDR_CHARGED, A_GST_CHARGED, 1))
    b = list(_lines("B", B_LINES, B_GROSS, _mdr(B_GROSS), _gst(_mdr(B_GROSS)), 1))
    c = list(_lines("C", C_LINES, C_GROSS, _mdr(C_GROSS), _gst(_mdr(C_GROSS)), 1))

    batches = [("setl_lakeviewA0001", A_SETTLED, a, A_STATED_REFUND),
               ("setl_lakeviewB0002", B_SETTLED, b, 0),
               ("setl_lakeviewC0003", C_SETTLED, c, 0)]

    # ---- razorpay_settlements.csv ----------------------------------------
    s = io.StringIO()
    w = csv.writer(s, lineterminator="\n")
    w.writerow(["settlement_id", "settled_on", "payment_id", "order_id",
                "gross_amount", "mdr", "gst_on_mdr", "net_amount", "row_type"])
    for sid, settled, lines, refund in batches:
        for ln in lines:
            w.writerow([sid, settled.isoformat(), ln["payment_id"], ln["order_id"],
                        _r(ln["gross"]), _r(ln["mdr"]), _r(ln["gst"]),
                        _r(ln["net"]), "captured"])
        if refund:
            w.writerow([sid, settled.isoformat(), "", "", _r(-refund), "0.00",
                        "0.00", _r(-refund), "refund_adjustment"])
    settlements = s.getvalue()

    # ---- bank_statement.csv ----------------------------------------------
    # Batch C is deliberately absent: settled by Razorpay, never credited.
    s = io.StringIO()
    w = csv.writer(s, lineterminator="\n")
    w.writerow(["value_date", "narration", "utr", "credit_amount", "debit_amount"])
    for sid, settled, lines, refund in batches:
        if sid.endswith("C0003"):
            continue
        net = sum(ln["net"] for ln in lines) - refund
        w.writerow([settled.isoformat(),
                    f"NEFT CR-HDFC0000060-RAZORPAY SOFTWARE PVT LTD-LAKEVIEW",
                    f"N2{abs(hash(sid)) % 10**11:011d}", _r(net), ""])
    bank = s.getvalue()

    # ---- orders.csv -------------------------------------------------------
    s = io.StringIO()
    w = csv.writer(s, lineterminator="\n")
    w.writerow(["order_id", "placed_on", "gross_amount", "channel", "payment_mode"])
    for _sid, settled, lines, _refund in batches:
        placed = settled - timedelta(days=2)
        for ln in lines:
            w.writerow([ln["order_id"], placed.isoformat(), _r(ln["gross"]),
                        "website", "upi"])
    w.writerow(["ORDCOD000001", "2026-08-03", _r(348_000), "instagram", "cod"])
    orders = s.getvalue()

    # ---- refunds.csv ------------------------------------------------------
    # Initiated so the contract's five-business-day netting rule lands it in
    # batch A. This is the merchant's own record, independent of the settlement
    # report -- which is the only reason the under-deduction can be detected.
    s = io.StringIO()
    w = csv.writer(s, lineterminator="\n")
    w.writerow(["refund_id", "payment_id", "order_id", "initiated_on", "amount"])
    w.writerow(["rfnd_lakeview00001", a[0]["payment_id"], a[0]["order_id"],
                "2026-08-07", _r(A_TRUE_REFUND)])
    refunds = s.getvalue()

    # ---- courier ----------------------------------------------------------
    cod_value, awb = 348_000, "AWB77310052991"
    cod_fee = cod_value * 150 // 10_000
    freight = 8_500                              # RTO freight on a delivered order
    s = io.StringIO()
    w = csv.writer(s, lineterminator="\n")
    w.writerow(["remittance_id", "remitted_on", "awb", "cod_value", "cod_fee",
                "rto_freight", "adjustment", "net_remitted"])
    w.writerow(["REM20260817", "2026-08-17", awb, _r(cod_value), _r(cod_fee),
                _r(freight), "0.00", _r(cod_value - cod_fee - freight)])
    cod = s.getvalue()

    s = io.StringIO()
    w = csv.writer(s, lineterminator="\n")
    w.writerow(["awb", "order_id", "shipped_on", "delivered_on", "cod_value", "status"])
    w.writerow([awb, "ORDCOD000001", "2026-08-05", "2026-08-09", _r(cod_value),
                "delivered"])
    shipments = s.getvalue()

    s = io.StringIO()
    w = csv.writer(s, lineterminator="\n")
    w.writerow(["dispute_id", "payment_id", "order_id", "raised_on", "amount"])
    disputes = s.getvalue()

    # ---- the two documents that define the contract ----------------------
    terms = {
        "merchant": MERCHANT, "period": PERIOD, "gateway": "Razorpay",
        "contracted_mdr_rate_pct": MDR_PCT, "gst_on_mdr_rate_pct": GST_PCT,
        "settlement_cycle": "T+2 business days",
        "refund_netting": ("Refunds are deducted from a later settlement batch, "
                           "typically 5 business days after initiation."),
        "courier_cod_fee_pct": COD_FEE_PCT, "courier_rto_freight_inr": RTO_FREIGHT,
        "cod_remittance_cycle": "Weekly, covering deliveries 7+ days prior",
        "match_key_note": ("settlement_id is authoritative. The bank UTR is "
                           "issued by the correspondent bank and is NOT a "
                           "Razorpay key."),
    }
    total_mdr = sum(ln["mdr"] for _s, _d, lines, _r_ in batches for ln in lines)
    invoice = {
        "invoice_number": "RZP/202608/LAKEVIEW/0001", "period": PERIOD,
        "supplier": "Razorpay Software Private Limited",
        "recipient": MERCHANT,
        "taxable_value": _r(total_mdr),
        "total_tax": _r(_gst(total_mdr)),
        "contracted_mdr_rate_pct": MDR_PCT, "gst_rate_pct": GST_PCT,
    }

    return {
        "razorpay_settlements.csv": settlements,
        "bank_statement.csv": bank,
        "orders.csv": orders,
        "refunds.csv": refunds,
        "cod_remittances.csv": cod,
        "shipments.csv": shipments,
        "disputes.csv": disputes,
        "contract_terms.json": json.dumps(terms, indent=2),
        "razorpay_mdr_invoice.json": json.dumps(invoice, indent=2),
    }


# ==========================================================================
def expected() -> dict:
    """What the arithmetic says the engine must find, computed here rather than
    copied from a previous run. `check()` compares the engine against this."""
    true_fee = (_mdr(A_GROSS) + _gst(_mdr(A_GROSS))) * A_LINES
    charged_fee = (A_MDR_CHARGED + A_GST_CHARGED) * A_LINES
    fee_overcharge = charged_fee - true_fee
    refund_under = A_TRUE_REFUND - A_STATED_REFUND
    return {
        "fee_overcharge_paise": fee_overcharge,
        "mdr_overcharge_paise": (A_MDR_CHARGED - _mdr(A_GROSS)) * A_LINES,
        "gst_overcharge_paise": (A_GST_CHARGED - _gst(_mdr(A_GROSS))) * A_LINES,
        "refund_under_paise": refund_under,
        "batch_variance_paise": abs(fee_overcharge - refund_under),
        "exposure_paise": fee_overcharge + refund_under,
        "lines_affected": A_LINES,
        "effective_mdr_pct": round(A_MDR_CHARGED / A_GROSS * 100, 2),
        "contract_mdr_pct": float(MDR_PCT),
        "batch_id": "setl_lakeviewA0001",
    }


def check() -> dict:
    """Run the real pipeline over the demo documents and assert the engine finds
    the pair. A demo that silently stops demonstrating its own point is worse
    than no demo, so this raises rather than warns."""
    import tempfile
    from pathlib import Path

    from . import audit as audit_mod
    from . import engine as engine_mod
    from .ingest import load, resolve

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sources"
        src.mkdir()
        for name, body in build().items():
            (src / name).write_text(body, encoding="utf-8")
        corpus = load(src)
        resolve(corpus)
        res = engine_mod.run(corpus)
        aud = audit_mod.run(corpus, res.batch_ties)

    exp = expected()
    pairs = [a for a in aud.overturned if a.hypothesis == "offsetting_pair"]
    if not pairs:
        raise AssertionError(
            "the demo no longer demonstrates a compensating pair — the "
            "adversarial pass found none")

    ev = pairs[0].evidence
    got_line = abs(ev["line_variance_paise"])
    got_refund = abs(ev["refund_variance_paise"])
    if got_line != exp["fee_overcharge_paise"]:
        raise AssertionError(
            f"fee overcharge drifted: engine {got_line}p, "
            f"arithmetic {exp['fee_overcharge_paise']}p")
    if got_refund != exp["refund_under_paise"]:
        raise AssertionError(
            f"refund variance drifted: engine {got_refund}p, "
            f"arithmetic {exp['refund_under_paise']}p")

    tied = sum(1 for v in res.batch_ties.values() if v)
    return {
        "batches": len(res.batch_ties), "tied": tied,
        "pairs": len(pairs),
        "line_variance_paise": ev["line_variance_paise"],
        "refund_variance_paise": ev["refund_variance_paise"],
        "residual_paise": ev["residual_paise"],
        **exp,
    }


if __name__ == "__main__":
    from .money import fmt

    r = check()
    print(f"\n  ATTEST — compensating error demo")
    print("  " + "=" * 62)
    print(f"  batches            {r['tied']}/{r['batches']} tie to the bank")
    print(f"  MDR overcharged    {fmt(r['mdr_overcharge_paise']):>14}   "
          f"({r['effective_mdr_pct']}% charged vs {r['contract_mdr_pct']}% contracted)")
    print(f"  GST on that        {fmt(r['gst_overcharge_paise']):>14}")
    print(f"  refund short by    {fmt(r['refund_under_paise']):>14}")
    print("  " + "-" * 62)
    print(f"  batch appears out  {fmt(r['batch_variance_paise']):>14}   "
          "<- what every total-level check sees")
    print(f"  actually wrong by  {fmt(r['exposure_paise']):>14}   "
          "<- what Attest reports")
    print(f"  understated by     {r['exposure_paise'] // max(r['batch_variance_paise'], 1):>13,}x\n")
