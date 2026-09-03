"""Match, then prove, then attack.

Three passes, deliberately separated, because the gap between them is the whole
argument of this project.

  MATCH  is what conventional reconciliation does: does the batch total agree
         with the bank credit? Cheap, fast, and it is the number everyone
         reports.

  PROVE  asks a harder question of every single line: can I trace this rupee
         from the order, through the fee calculation, into the batch, into the
         bank? A line is proven only when no link in that chain is missing.

  ATTACK (in audit.py) tries to break the matches that survived.

A batch total can tie while every line inside it is wrong. That is not a
hypothetical -- it is what a compensating error is. So MATCH is reported for
contrast and never trusted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from .ingest import Batch, Corpus, SettlementRow, CodRow
from .money import pct


def _business_days_before(d, n: int):
    from datetime import timedelta
    cur, back = d, 0
    while back < n:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            back += 1
    return cur

# A batch is considered "tied" by conventional reconciliation if the bank credit
# agrees with the report to within a rupee. This is a generous but entirely
# ordinary tolerance -- and it is exactly the gap a compensating pair hides in.
BATCH_TOLERANCE = 100          # paise
LINE_TOLERANCE = 0             # lines must be exact; drift is a finding, not noise


@dataclass
class ProofChain:
    """The evidence trail for one line. Proven only if every stage holds."""
    subject_id: str
    subject_type: str
    stages: dict[str, bool] = field(default_factory=dict)
    broke_at: str | None = None
    detail: str = ""
    delta: int = 0

    @property
    def complete(self) -> bool:
        return all(self.stages.values())

    def check(self, stage: str, ok: bool, detail: str = "", delta: int = 0):
        self.stages[stage] = ok
        if not ok and self.broke_at is None:
            self.broke_at, self.detail, self.delta = stage, detail, delta
        return ok


@dataclass
class Result:
    batch_ties: dict[str, bool] = field(default_factory=dict)
    gateway_chains: list[ProofChain] = field(default_factory=list)
    cod_chains: list[ProofChain] = field(default_factory=list)
    batch_findings: list[dict] = field(default_factory=list)

    @property
    def chains(self) -> list[ProofChain]:
        return self.gateway_chains + self.cod_chains

    def match_rate(self) -> float:
        """The conventional number: lines sitting inside a batch that ties."""
        total = len(self.chains)
        if not total:
            return 0.0
        tied = sum(
            1 for c in self.gateway_chains
            if self.batch_ties.get(c.stages.get("_batch_id", ""), True)
        )
        return 0.0 if not total else tied / total

    def proof_rate(self) -> float:
        total = len(self.chains)
        return (sum(1 for c in self.chains if c.complete) / total) if total else 0.0


# ==========================================================================
# Pass 1 -- conventional batch matching
# ==========================================================================
def match_batches(corpus: Corpus) -> dict[str, bool]:
    """Does each batch total agree with the bank credit it resolved to?"""
    credit_of = {b.settlement_id: b.credit for b in corpus.bank if b.settlement_id}
    ties: dict[str, bool] = {}
    for sid, batch in corpus.batches.items():
        got = credit_of.get(sid)
        ties[sid] = got is not None and abs(got - batch.expected_credit) <= BATCH_TOLERANCE
    return ties


# ==========================================================================
# Pass 2 -- line-level proof
# ==========================================================================
def prove_gateway(corpus: Corpus, ties: dict[str, bool]) -> list[ProofChain]:
    mdr_rate = Decimal(corpus.terms["contracted_mdr_rate_pct"])
    gst_rate = Decimal(corpus.terms["gst_on_mdr_rate_pct"])
    credited = {b.settlement_id for b in corpus.bank if b.settlement_id}

    chains: list[ProofChain] = []
    for sid, batch in corpus.batches.items():
        for ln in batch.lines:
            c = ProofChain(subject_id=ln.payment_id, subject_type="settlement_line")
            c.stages["_batch_id"] = sid   # carried for reporting, not a check

            # 1. the order this line claims to settle actually exists, at this value
            o = corpus.orders.get(ln.order_id)
            c.check(
                "order", o is not None and o.gross == ln.gross,
                "no matching order at this value" if o is None
                else f"order gross {o.gross} vs settled gross {ln.gross}",
                0 if o is None else ln.gross - o.gross,
            )

            # 2. the fee was calculated at the contracted rate
            exp_mdr = pct(ln.gross, mdr_rate)
            c.check(
                "mdr", abs(ln.mdr - exp_mdr) <= LINE_TOLERANCE,
                f"MDR {ln.mdr}p charged, {exp_mdr}p contracted", ln.mdr - exp_mdr,
            )

            # 3. tax was computed on the right base
            exp_gst = pct(ln.mdr, gst_rate)
            c.check(
                "gst", abs(ln.gst_on_mdr - exp_gst) <= LINE_TOLERANCE,
                f"GST {ln.gst_on_mdr}p charged, {exp_gst}p on this MDR",
                ln.gst_on_mdr - exp_gst,
            )

            # 4. the arithmetic of the line closes
            exp_net = ln.gross - ln.mdr - ln.gst_on_mdr
            c.check("net", ln.net == exp_net,
                    f"net {ln.net}p vs computed {exp_net}p", ln.net - exp_net)

            # 5. the batch this line sits in actually arrived in the bank
            # Exposure is this line's own net, not the whole batch -- otherwise a
            # 22-line batch reports its value 22 times and the headline is nonsense.
            c.check("credit", sid in credited,
                    "settlement has no corresponding bank credit",
                    ln.net if sid not in credited else 0)

            chains.append(c)
    return chains


def prove_cod(corpus: Corpus) -> list[ProofChain]:
    fee_rate = Decimal(corpus.terms["courier_cod_fee_pct"])
    freight = int(Decimal(corpus.terms["courier_rto_freight_inr"]) * 100)

    chains: list[ProofChain] = []
    for r in corpus.cod:
        c = ProofChain(subject_id=r.awb, subject_type="cod_remittance_line")
        sh = corpus.shipments.get(r.awb)

        c.check("shipment", sh is not None, "AWB not in shipment manifest")
        c.check(
            "cod_value", sh is not None and sh.cod_value == r.cod_value,
            "remitted COD value disagrees with the manifest",
            0 if sh is None else r.cod_value - sh.cod_value,
        )

        exp_fee = pct(r.cod_value, fee_rate)
        c.check("cod_fee", abs(r.cod_fee - exp_fee) <= LINE_TOLERANCE,
                f"COD fee {r.cod_fee}p vs contracted {exp_fee}p", r.cod_fee - exp_fee)

        exp_freight = freight if (sh and sh.status == "rto") else 0
        c.check("freight", r.rto_freight == exp_freight,
                f"RTO freight {r.rto_freight}p vs expected {exp_freight}p",
                r.rto_freight - exp_freight)

        # An adjustment with no AWB-level justification is unproveable by
        # construction. It is not an arithmetic error -- it is a missing reason.
        c.check("adjustment", r.adjustment == 0,
                "unreferenced adjustment on the remittance line", r.adjustment)

        exp_net = r.cod_value - r.cod_fee - r.rto_freight + r.adjustment
        c.check("net", r.net == exp_net,
                f"net {r.net}p vs computed {exp_net}p", r.net - exp_net)

        chains.append(c)
    return chains


# ==========================================================================
# Batch-level findings the line pass cannot see
# ==========================================================================
def check_batches(corpus: Corpus) -> list[dict]:
    findings: list[dict] = []
    refunds_by_settlement: dict[str, int] = {}

    # Refunds net against a LATER batch -- typically 5 business days on. So a
    # refund initiated near the period end is deducted next month and is simply
    # not expected here. Comparing raw totals flags every one of those as an
    # error, which is the classic false positive in this domain. We compare only
    # against refunds whose deduction was actually due inside the period.
    # Anchor on the declared period, NOT on max(settled_on). A single out-of-period
    # record must not be able to redefine the month being closed.
    from datetime import date as _date
    y, m = (int(x) for x in corpus.mdr_invoice["period"].split("-"))
    period_end = _date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
    in_scope = [b for b in corpus.batches.values() if b.settled_on <= period_end]
    last_settled = max(b.settled_on for b in in_scope) if in_scope else period_end
    cutoff = _business_days_before(last_settled, 5)

    in_period = [r for r in corpus.refunds if r.initiated_on <= cutoff]
    deferred = [r for r in corpus.refunds if r.initiated_on > cutoff]
    total_refund_adj = -sum(b.refund_adj for b in corpus.batches.values())
    total_refunds = sum(r.amount for r in in_period)
    if deferred:
        findings.append({
            "class": "TIMING_NOT_ERROR",
            "detail": (
                f"{len(deferred)} refunds initiated after {cutoff} net against "
                "next period's settlements -- expected, not a variance"
            ),
            "delta": 0,
            "container": "period",
        })
    if total_refund_adj != total_refunds:
        findings.append({
            "class": "REFUND_MISMATCH",
            "detail": (
                f"settlements deduct {total_refund_adj}p of refunds against "
                f"{total_refunds}p recorded in the refund export"
            ),
            "delta": total_refund_adj - total_refunds,
            "container": "period",
        })

    total_cb_adj = -sum(b.chargeback_adj for b in corpus.batches.values())
    total_disputes = sum(d.amount for d in corpus.disputes if d.raised_on <= last_settled)
    if total_cb_adj != total_disputes:
        findings.append({
            "class": "CHARGEBACK_ORPHAN",
            "detail": (
                f"settlements deduct {total_cb_adj}p of chargebacks against "
                f"{total_disputes}p in the dispute export"
            ),
            "delta": total_cb_adj - total_disputes,
            "container": "period",
        })

    for sid, b in corpus.batches.items():
        if b.hold_release:
            findings.append({
                "class": "UNREFERENCED_ADJ",
                "detail": "hold released into the batch with no order reference",
                "delta": b.hold_release,
                "container": sid,
            })

    # The GST the merchant will claim as input credit must tie to the GST
    # actually deducted across the month's settlements.
    inv_gst = int(Decimal(corpus.mdr_invoice["total_tax"]) * 100)
    settled_gst = sum(
        l.gst_on_mdr for b in corpus.batches.values() for l in b.lines
    )
    if inv_gst != settled_gst:
        findings.append({
            "class": "ITC_MISMATCH",
            "detail": (
                f"MDR tax invoice shows {inv_gst}p of GST against {settled_gst}p "
                "deducted in settlements -- input credit at risk"
            ),
            "delta": inv_gst - settled_gst,
            "container": "mdr_invoice",
        })

    # --- generic integrity checks -----------------------------------------
    seen: dict[str, str] = {}
    for sid, b in corpus.batches.items():
        for ln in b.lines:
            if ln.payment_id in seen and seen[ln.payment_id] != sid:
                findings.append({
                    "class": "DUPLICATE_SETTLEMENT_LINE",
                    "detail": (
                        f"payment {ln.payment_id} settled in both "
                        f"{seen[ln.payment_id]} and {sid} -- revenue counted twice"
                    ),
                    "delta": ln.net, "container": sid,
                })
            seen[ln.payment_id] = sid

    awb_seen: set[str] = set()
    for r in corpus.cod:
        key = f"{r.remittance_id}:{r.awb}"
        if key in awb_seen:
            findings.append({
                "class": "DUPLICATE_AWB",
                "detail": f"AWB {r.awb} remitted twice in {r.remittance_id}",
                "delta": r.net, "container": r.remittance_id,
            })
        awb_seen.add(key)

    for bc in corpus.bank:
        if not bc.settlement_id:
            findings.append({
                "class": "ORPHAN_BANK_CREDIT",
                "detail": (
                    f"credit of {bc.credit}p on {bc.value_date} resolves to no "
                    "settlement -- unexplained money in is still unexplained"
                ),
                "delta": bc.credit, "container": bc.utr,
            })

    return findings


def run(corpus: Corpus) -> Result:
    ties = match_batches(corpus)
    return Result(
        batch_ties=ties,
        gateway_chains=prove_gateway(corpus, ties),
        cod_chains=prove_cod(corpus),
        batch_findings=check_batches(corpus),
    )
