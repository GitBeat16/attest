"""Render the attestation as a single self-contained HTML close pack.

No server, no build step, no dependencies. The data is inlined, so the file opens
by double-clicking, commits to docs/ for GitHub Pages, or goes in an email.

The page is written for someone who has never seen this project and has about
sixty seconds. So it leads with the money, shows the one finding that makes the
argument, and only then explains the method. Every technical class name carries a
plain-English label; the code is secondary, for people who want it.
"""
from __future__ import annotations

from .money import fmt

# Plain-English names. The internal codes are precise and unreadable; a reader
# should never have to decode SELF_REFERENTIAL_TIE to follow the page.
LABELS = {
    "MDR": ("Gateway fee charged above contract", "Razorpay applied a rate higher than the agreed one"),
    "GST": ("Tax calculated on the wrong base", "GST computed on the transaction, not on the fee"),
    "CREDIT": ("Settlement never reached the bank", "The report says it was paid; no credit arrived"),
    "ADJUSTMENT": ("Courier paid short", "An unexplained deduction with no AWB-level reason"),
    "NET": ("Line does not add up", "The stated net differs from its own components"),
    "ORDER": ("No matching order", "The settled line refers to an order we cannot find"),
    "COD_VALUE": ("COD value disagrees with manifest", "Remitted amount differs from what was shipped"),
    "COD_FEE": ("COD fee above contract", "Courier fee higher than the agreed rate"),
    "FREIGHT": ("Wrong return freight", "RTO freight charged where it should not apply"),
    "SHIPMENT": ("AWB not in the manifest", "Courier billed for a shipment we have no record of"),
    "SELF_REFERENTIAL_TIE": ("Batch tied, but its fees are wrong", "The bank matched the report — and the report was wrong"),
    "OFFSETTING_PAIR": ("Two errors that cancelled out", "Invisible to every total-level check"),
    "TOLERANCE_ABUSE": ("Rounding that always favours one side", "Individually tiny, systematically one-directional"),
    "UNREFERENCED_ADJ": ("Adjustment with no reference", "Money moved with no order or payment attached"),
    "CHARGEBACK_ORPHAN": ("Chargeback with no dispute record", "Deducted from settlement, absent from the dispute export"),
    "REFUND_MISMATCH": ("Refund deducted more than once", "The same refund netted twice"),
    "DUPLICATE_AWB": ("Shipment remitted twice", "One AWB appears twice in a courier statement"),
    "DUPLICATE_SETTLEMENT_LINE": ("Payment settled twice", "Revenue counted twice across two batches"),
    "ORPHAN_BANK_CREDIT": ("Money in with no explanation", "A credit no settlement accounts for"),
    "OUT_OF_PERIOD_SETTLEMENT": ("Settlement outside the period", "Would overstate the month if pulled in"),
}

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --paper:#FCFBF8; --surface:#F4F1EA; --raised:#EEEAE0;
  --ink:#16181D; --ink2:#4A4E58; --ink3:#7B808C;
  --rule:#E3DED3; --rule2:#CFC8B8;
  --accent:#1B4B73; --accent-soft:#E7EDF3;
  --good:#1D6B4F; --good-soft:#E4EFE9;
  --warn:#8A5A12; --warn-soft:#F5EDDC;
  --bad:#A03A22; --bad-soft:#F7E7E1;
  --serif:Newsreader,Georgia,"Times New Roman",serif;
  --sans:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);
  font-size:15.5px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto;padding:0 32px}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
a{color:var(--accent)}

/* nav, matching the landing page so this reads as one product */
nav{border-bottom:1px solid var(--rule);background:var(--paper)}
nav .wrap{display:flex;align-items:center;justify-content:space-between;height:62px}
.mark{display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--ink)}
.mark .glyph{width:26px;height:26px;border:1.5px solid var(--accent);border-radius:3px;
  display:grid;place-items:center;font-family:var(--mono);font-size:13px;font-weight:600;
  color:var(--accent)}
.mark .name{font-family:var(--mono);font-size:14px;letter-spacing:.22em;
  text-transform:uppercase;font-weight:600}
nav .meta{font-family:var(--mono);font-size:12px;color:var(--ink3);text-align:right;
  line-height:1.5}

header.page{padding:56px 0 44px;border-bottom:1px solid var(--rule)}
.eyebrow{font-family:var(--mono);font-size:11.5px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--accent);margin-bottom:18px}
h1{font-family:var(--serif);font-size:clamp(2rem,4.6vw,2.9rem);line-height:1.12;
  font-weight:500;letter-spacing:-.02em;max-width:20ch}
h1 b{font-weight:500;color:var(--good)} h1 i{font-style:normal;color:var(--bad)}
header.page p{color:var(--ink2);max-width:60ch;margin-top:18px;font-size:1.04rem}

section{padding:56px 0;border-bottom:1px solid var(--rule)}
h2{font-family:var(--serif);font-size:1.65rem;font-weight:500;letter-spacing:-.015em;
  line-height:1.2;margin-bottom:6px}
.lede{color:var(--ink2);max-width:64ch;margin-bottom:20px;font-size:.98rem}
.step{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink3);margin-bottom:12px}

.split{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--rule);
  border:1px solid var(--rule);margin-top:22px}
.split > div{background:var(--paper);padding:22px 22px}
.split .l{font-family:var(--mono);font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink3)}
.split .v{font-family:var(--mono);font-size:1.75rem;font-weight:600;margin-top:10px;
  letter-spacing:-.025em}
.split .n{font-size:.87rem;color:var(--ink3);margin-top:8px;line-height:1.45}

.case{border:1px solid var(--rule);background:var(--surface)}
.case .body{padding:26px 26px}
.eq{font-family:var(--mono);font-size:14.5px;max-width:560px}
.eq .row{display:flex;justify-content:space-between;gap:20px;padding:9px 0;
  border-bottom:1px dotted var(--rule2)}
.eq .lab{color:var(--ink2)} .eq .amt{font-weight:600;white-space:nowrap;color:var(--bad)}
.eq .amt.g{color:var(--good)}
.eq .tot{border-bottom:none;margin-top:8px;padding-top:14px;border-top:1.5px solid var(--ink)}
.eq .tot .lab{color:var(--ink);font-weight:600} .eq .tot .amt{color:var(--warn)}
.verdict{margin-top:22px;padding-top:20px;border-top:1px solid var(--rule);
  color:var(--ink2);font-size:.96rem;max-width:66ch}
.verdict b{color:var(--ink);font-weight:600}

.descent{border:1px solid var(--rule);background:var(--paper)}
.dline{display:flex;align-items:center;gap:20px;padding:18px 24px;
  border-bottom:1px solid var(--rule)}
.dline:last-child{border-bottom:none}
.dnum{font-family:var(--mono);font-size:1.9rem;font-weight:600;min-width:110px;
  letter-spacing:-.03em}
.dtrack{flex:1;height:5px;background:var(--raised);border-radius:3px;overflow:hidden;
  min-width:70px}
.dtrack i{display:block;height:100%}
.dtext{min-width:300px;font-size:.93rem;color:var(--ink2);line-height:1.45}
.dtext b{color:var(--ink);display:block;font-weight:600;font-size:1rem;
  font-family:var(--serif)}
.c1{color:var(--ink3)} .c1t{background:var(--ink3)}
.c2{color:var(--good)} .c2t{background:var(--good)}
.c3{color:var(--bad)} .c3t{background:var(--bad)}

table{width:100%;border-collapse:collapse;font-size:.93rem}
th{background:var(--surface);text-align:left;font-family:var(--mono);font-size:10.5px;
  letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);font-weight:500;
  padding:11px 15px;border-bottom:1px solid var(--rule2)}
td{padding:12px 15px;border-bottom:1px solid var(--rule);color:var(--ink2);
  vertical-align:top}
tr:last-child td{border-bottom:none}
td.k{color:var(--ink);font-weight:500}
td.k small{display:block;color:var(--ink3);font-size:11.5px;margin-top:3px;font-weight:400}
td.n,th.n{font-family:var(--mono);text-align:right;white-space:nowrap;
  font-variant-numeric:tabular-nums}
.tbl{border:1px solid var(--rule);background:var(--paper);overflow-x:auto;margin-top:8px}
tr.miss td{background:var(--bad-soft)}

.tag{display:inline-block;font-family:var(--mono);font-size:10px;letter-spacing:.09em;
  text-transform:uppercase;padding:3px 7px;border-radius:3px;font-weight:500;
  white-space:nowrap}
.t-ok{background:var(--good-soft);color:var(--good)}
.t-miss{background:var(--bad-soft);color:var(--bad)}
.t-held{background:var(--accent-soft);color:var(--accent)}
.t-urg{background:var(--bad-soft);color:var(--bad)}
.t-soon{background:var(--warn-soft);color:var(--warn)}

.note{background:var(--accent-soft);border-left:3px solid var(--accent);padding:18px 22px;
  margin-top:16px;font-size:.95rem;color:var(--ink2);line-height:1.6;max-width:74ch}
.note b{color:var(--ink);font-weight:600}
.note.warnb{background:var(--warn-soft);border-left-color:var(--warn)}

.status{border:1px solid var(--rule);background:var(--surface);padding:24px 26px;
  display:flex;gap:26px;align-items:center;flex-wrap:wrap}
.status .big{font-family:var(--mono);font-size:1.3rem;font-weight:600;color:var(--warn);
  letter-spacing:-.01em}
.status .why{color:var(--ink2);font-size:.95rem;max-width:62ch;line-height:1.55}
.status .why b{color:var(--ink)}

footer{padding:38px 0 64px;font-family:var(--mono);font-size:11.5px;color:var(--ink3);
  line-height:1.9}
@media(max-width:760px){.split{grid-template-columns:1fr}.dline{flex-wrap:wrap}
  .dtext{min-width:0}}

.sealbox{border:1px solid var(--rule);background:var(--surface);padding:22px 24px;
  margin-top:20px}
.sealbox .sd{font-family:var(--mono);font-size:1.05rem;font-weight:600;
  letter-spacing:.08em;color:var(--ink);word-break:break-all}
.sealbox .sm{font-family:var(--mono);font-size:11.5px;color:var(--ink3);margin-top:10px}
.sealbox .sc{font-family:var(--mono);font-size:12px;color:var(--accent);margin-top:14px;
  padding-top:12px;border-top:1px solid var(--rule2)}

/* ================= responsive ================= */
h2{scroll-margin-top:70px}
@media(max-width:820px){
  .seal2,.grid2,.two{grid-template-columns:1fr!important}
}
@media(max-width:640px){
  .wrap{padding:0 18px}
  nav .wrap{flex-direction:column;align-items:flex-start;gap:6px;height:auto;padding-top:12px;
    padding-bottom:12px}
  nav .meta{text-align:left}
  h1{font-size:1.6rem}
  h2{font-size:1.3rem}
  .sealbox{padding:18px 16px}
  .sealbox .sd{font-size:.9rem;letter-spacing:.03em}
  .sealbox .sc{font-size:11px;overflow-x:auto}
  .status{flex-direction:column;align-items:flex-start;gap:10px}

  /* Four columns of financial data do not fit a phone, and scrolling sideways
     hides the column the reader came for. Each row becomes a labelled block. */
  .tbl{border:none;overflow-x:visible;background:transparent}
  .tbl table,.tbl tbody,.tbl tr,.tbl td{display:block;width:100%}
  .tbl thead{display:none}
  .tbl tr{border:1px solid var(--rule);background:var(--paper);margin-bottom:12px}
  .tbl td{border:none;padding:9px 15px;text-align:left}
  .tbl td+td{border-top:1px dotted var(--rule)}
  .tbl td::before{content:attr(data-l);display:block;font-family:var(--mono);
    font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);
    margin-bottom:2px}
  .tbl td:first-child::before{content:none}
  .tbl td.k{font-size:1.02rem;padding-top:13px}
  .tbl td.n{text-align:left;font-size:1.08rem;font-weight:600}
  .tbl tr.miss{background:var(--bad-soft)}
}

.logo{width:24px;height:24px;color:var(--accent);flex:none}
footer .fgrid{display:grid;grid-template-columns:1.6fr 1fr 1fr;gap:34px;padding-bottom:34px}
footer h5{font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink3);font-weight:500;margin-bottom:13px}
footer ul{list-style:none;margin:0;padding:0}
footer li{margin-bottom:8px}
footer .fbrand p{color:var(--ink3);font-size:.88rem;max-width:36ch;line-height:1.55;margin-top:10px}
footer .fbase{border-top:1px solid var(--rule);padding-top:18px;display:flex;
  justify-content:space-between;gap:18px;flex-wrap:wrap;font-family:var(--mono);
  font-size:11px;color:var(--ink3)}
@media(max-width:700px){footer .fgrid{grid-template-columns:1fr;gap:26px}}
"""


def _pc(x: float) -> str:
    return f"{x*100:.1f}%"


def _label(cls: str) -> tuple[str, str]:
    return LABELS.get(cls, (cls.replace("_", " ").title(), ""))


def esc(v) -> str:
    """Escape anything that came from outside this program.

    The close pack is a document a merchant hands to their auditor, which makes
    it a delivery vehicle: a business name or a settlement id containing markup
    would otherwise execute in the reader's browser. Every value interpolated
    below that originated in a form field or an uploaded file goes through here.
    """
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render(p: dict) -> str:
    from .seal import MARK_CLOSE, MARK_OPEN, PH, PH_GROUPED, canonical
    seal_canon = canonical(p)
    seal_blob = (MARK_OPEN + PH + " "
                 + __import__("json").dumps(seal_canon, sort_keys=True,
                                            separators=(",", ":"))
                 + MARK_CLOSE)
    rc = p.get("recovery") or {}
    comp = (p.get("compensating") or [{}])[0]

    # --- the worked example, with real numbers from the run ----------------
    if comp:
        lv = abs(comp.get("line_variance_paise", 0))
        rv = abs(comp.get("refund_variance_paise", 0))
        res = comp.get("residual_paise", 0)
        case = f"""
<div class="case">
  <div class="eq">
    <div class="row"><span class="lab">Gateway fee overcharged on one line</span>
      <span class="amt">+{fmt(lv)}</span></div>
    <div class="row"><span class="lab">Refund under-deducted in the same batch</span>
      <span class="amt g">&minus;{fmt(rv)}</span></div>
    <div class="row tot"><span class="lab">What the batch total looks like</span>
      <span class="amt">{fmt(abs(res))} out</span></div>
  </div>
  <div class="verdict">
    Two real errors. The batch is out by <b>{fmt(abs(res))}</b> &mdash; inside any
    tolerance a finance team would set, so every automated check passes and the
    money quietly leaves. Accounting calls this a <b>compensating error</b>, and a
    total-level check cannot detect it by construction.
    <br><br>
    Attest catches it by rebuilding what the refund deduction <b>should</b> have
    been from the merchant's own refund records and the netting rule in the
    contract &mdash; never from the settlement report it is auditing. Batch
    <span class="mono" style="color:var(--ink)">{esc(comp.get('target',''))[:22]}</span>.
  </div>
</div>"""
    else:
        case = '<div class="case"><div class="verdict">No compensating pairs in this run.</div></div>'

    # --- the descent, all three over the same population where possible ----
    lm = p.get("line_match_rate", p["match_rate"])
    descent = f"""
<div class="descent">
  <div class="dline">
    <span class="dnum c1">{_pc(lm)}</span>
    <span class="dtrack"><i class="c1t" style="width:{lm*100:.1f}%"></i></span>
    <span class="dtext"><b>Looks reconciled</b>
      {p.get('lines_in_tied', 0):,} of {p['lines']:,} lines sit in a batch whose
      total agrees with the bank.</span>
  </div>
  <div class="dline">
    <span class="dnum c2">{_pc(p['proof_rate'])}</span>
    <span class="dtrack"><i class="c2t" style="width:{p['proof_rate']*100:.1f}%"></i></span>
    <span class="dtext"><b>Actually provable</b>
      {p['proven']:,} of {p['lines']:,} lines trace end to end: order &rarr; fee
      &rarr; tax &rarr; batch &rarr; bank, with no link missing.</span>
  </div>
  <div class="dline">
    <span class="dnum c3">{p['overturned']}</span>
    <span class="dtrack"><i class="c3t" style="width:{p['false_match_rate']*100:.1f}%"></i></span>
    <span class="dtext"><b>Matches we broke on purpose</b>
      Of {p['tested']} batches that passed, {p['overturned']} failed adversarial
      review &mdash; {_pc(p['false_match_rate'])}. Published deliberately.</span>
  </div>
</div>
<div class="note"><b>Why the first number is a lie.</b> Razorpay pays exactly what
  its own settlement report says it will pay, so the bank and the report agree by
  construction. That tie proves the money <i>arrived</i>. It proves nothing about
  whether the deductions inside were <i>correct</i> &mdash; and it is the number
  conventional reconciliation reports as success.</div>"""

    # --- what to do about it ----------------------------------------------
    dl_rows = "".join(
        f"<tr><td class='mono'>{d['deadline']}</td>"
        f"<td class='n' data-l='Days left'><span class='tag "
        f"{'t-urg' if d['urgency'] in ('critical','lapsed') else 't-soon' if d['urgency']=='urgent' else 't-ok'}'>"
        f"{d['days']} days</span></td>"
        f"<td class='k' data-l='What happened'>{esc(_label(d['cls'])[0])}"
        f"<small>{esc(_label(d['cls'])[1])}</small></td>"
        f"<td class='n' data-l='Exposure'>{fmt(d['exposure'])}</td>"
        f"<td data-l='Counterparty'>{esc(d['party'])}</td></tr>"
        for d in rc.get("deadlines", [])
    )

    ex_rows = "".join(
        f"<tr><td class='k'>{esc(_label(e['class'])[0])}<small>{esc(_label(e['class'])[1])}</small></td>"
        f"<td class='n' data-l='Items'>{e['count']}</td>"
        f"<td class='n' data-l='Exposure'>{fmt(e['exposure'])}</td>"
        f"<td data-l='Evidence required'>{esc(e['evidence_required'])}</td></tr>"
        for e in p["exceptions"][:8]
    )

    sc_rows = ""
    for cls, s in sorted((p.get("scorecard") or {}).items(),
                         key=lambda x: (not x[1]["held_out"], x[0])):
        held, missed = s["held_out"], s["detected"] == 0
        tag = ("<span class='tag t-miss'>held out &middot; missed</span>" if held and missed
               else "<span class='tag t-held'>held out &middot; found</span>" if held
               else "<span class='tag t-ok'>designed for</span>")
        sc_rows += (f"<tr class='{'miss' if held and missed else ''}'>"
                    f"<td class='k'>{_label(cls)[0]}</td>"
                    f"<td class='n' data-l='Planted'>{s['planted']}</td>"
                    f"<td class='n' data-l='Found'>{s['detected']}</td>"
                    f"<td class='n' data-l='Recall'>{s['recall']*100:.0f}%</td>"
                    f"<td data-l='Provenance'>{tag}</td></tr>")

    if p.get("scorecard"):
        accuracy_section = f"""<h2>Why you can trust these numbers</h2>
<p class="lede">The test data was built truth-first: the real ledger was generated
  and frozen, the documents derived from it, then errors injected into the
  documents only. The pipeline never reads the answer key.</p>
<div class="tbl"><table>
<thead><tr><th>Error type</th><th class="n">Planted</th><th class="n">Found</th>
<th class="n">Recall</th><th>Provenance</th></tr></thead>
<tbody>{sc_rows}</tbody></table></div>
<div class="note warnb"><b>The row in red is the honest one.</b> Four error types
  were planted with <b>no detector written for them</b>. Three were caught anyway
  by generic integrity checks &mdash; that is the only real evidence this
  generalises. One was missed, and is reported as missed. Recall on the types we
  designed for is {_pc(p['designed_recall'])}, which on its own would prove
  nothing at all. Recall on the held-out types is
  <b>{_pc(p['holdout_recall'])}</b>. That is the number that means something.</div>"""
    else:
        accuracy_section = """<h2>What this close pack does not claim</h2>
<p class="lede">This ran against your own data, so there is no answer key. Attest
  can tell you that something does not reconcile. It cannot tell you it found
  <i>everything</i> &mdash; nobody knows the correct answer for a real month, and
  any tool that quotes you an accuracy figure on your own data is quoting you a
  number it cannot possibly have measured.</p>
<div class="note warnb"><b>Recall is measured once, on a corpus whose truth was
  constructed.</b> On that benchmark, four error types were planted with no
  detector written for them; three were caught by generic checks and one was
  missed and is published as missed. That figure belongs to the benchmark and is
  not transferred to this page. What is on this page is evidence: what tied, what
  traced end to end, and what did not.</div>"""

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Attest — close pack {esc(p['period'])}</title>
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="alternate icon" href="favicon-32.png" sizes="32x32">
<meta name="theme-color" content="#1B4B73">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{CSS}</style></head><body>

<nav><div class="wrap">
  <a class="mark" href="index.html"><svg class="logo" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M8 20.6 L16 4.6 L24 20.6" stroke="currentColor" stroke-width="2.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M11.4 15.7 H20.6" stroke="currentColor" stroke-width="2.7" stroke-linecap="round"/><path d="M8 24.6 H24" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><path d="M8 27.8 H24" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg><span class="name">Attest</span></a>
  <div class="meta">{esc(p['merchant'])}<br>{esc(p['period'])} &nbsp;·&nbsp; {p['records']:,} records
    from 7 systems &nbsp;·&nbsp; closed in {p['seconds']}s</div>
</div></nav>

<div class="wrap">
<header class="page">
  <p class="eyebrow">Close pack &middot; generated {p['generated']}</p>
  <h1>We found <b>{fmt(rc.get('recoverable', 0))}</b> you can still claim back
    &mdash; and <i>{fmt(rc.get('monthly_lapsed', 0))}</i> of it dies if you close
    monthly.</h1>
  <p>This month's books tie to the bank. They are still wrong. Below is what was
    taken that shouldn't have been, who to claim it from, and how long you have.</p>
</header>

<div class="split">
  <div><div class="l">Claimable now</div>
    <div class="v" style="color:var(--good)">{fmt(rc.get('recoverable', 0))}</div>
    <div class="n">across {rc.get('recoverable_count', 0)} items, from three counterparties</div></div>
  <div><div class="l">Expires within 7 days</div>
    <div class="v" style="color:var(--warn)">{fmt(rc.get('expiring_soon', 0))}</div>
    <div class="n">{rc.get('expiring_count', 0)} claim windows closing</div></div>
  <div><div class="l">Lost by closing monthly</div>
    <div class="v" style="color:var(--bad)">{fmt(rc.get('monthly_lapsed', 0))}</div>
    <div class="n">{rc.get('monthly_lapsed_count', 0)} claims expire unfiled at a
      {rc.get('late_date','')} close</div></div>
</div>

<h2>The error a match rate can never find</h2>
<p class="lede">One batch from this month, in full. It reconciles perfectly.</p>
{case}

<h2>So we stopped trusting the match rate</h2>
<p class="lede">The same month, measured three ways. The number goes down as the
  test gets harder &mdash; which is the point.</p>
{descent}

<h2>What to chase, and by when</h2>
<p class="lede">Courier disputes close in 14 days. A month-end close surfaces a
  day-8 finding on day 31, by which time the money is gone.</p>
<div class="tbl"><table>
<thead><tr><th>Deadline</th><th class="n">Left</th><th>What happened</th>
<th class="n">Amount</th><th>Claim from</th></tr></thead>
<tbody>{dl_rows}</tbody></table></div>

<h2>Everything unresolved</h2>
<p class="lede">Each one says what evidence would close it &mdash; not just that it
  didn't match.</p>
<div class="tbl"><table>
<thead><tr><th>What happened</th><th class="n">Items</th><th class="n">Exposure</th>
<th>Evidence required</th></tr></thead>
<tbody>{ex_rows}</tbody></table></div>

{accuracy_section}

<h2>Attestation</h2>
<div class="status">
  <span class="big">{'SIGNED' if p['signed'] else 'NOT ATTESTABLE'}</span>
  <span class="why">{fmt(p['residual_paise'])} of {fmt(p['volume'])} cannot be
    attributed to any cause &mdash; {p['residual_bps']} bps of volume, against a
    25 bps limit. The close stays open until that is investigated.
    <b>Refusing to certify is the point of an attestation:</b> a system that always
    signs is not attesting to anything.</span>
</div>

<h2>Seal</h2>
<p class="lede">Every figure above is covered by this digest. Change one rupee
  anywhere on the page and it stops matching.</p>
<div class="sealbox">
  <div class="sd">{PH_GROUPED}</div>
  <div class="sm">SHA-256 over {seal_canon['records']:,} records ·
    {seal_canon['tied']}/{seal_canon['batches']} batches tied ·
    {seal_canon['proven']:,}/{seal_canon['lines']} lines proven ·
    residual {fmt(seal_canon['residual_paise'])}</div>
  <div class="sc">python3 -m attest.seal --verify &lt;this file&gt;</div>
</div>
<div class="note"><b>What this proves, and what it does not.</b> A matching
  digest proves the pack has not been edited since it was sealed. It does
  <i>not</i> prove who produced it — anyone holding the code can seal a
  document, which is true of every self-contained checksum and is why the honest
  word is <b>tamper-evident</b> rather than tamper-proof. Authorship comes from
  the close record, which is written once and can never be updated or deleted,
  by its owner or by us. An auditor asks one question of it: was this exact
  digest recorded, and when?</div>

{seal_blob}

<footer>
<div class="fgrid">
  <div class="fbrand">
    <b style="color:var(--ink)">Attest</b>
    <p>Reports what a close can prove, not what it happened to match.
      Deterministic engine, zero runtime dependencies, every figure reproducible.</p>
  </div>
  <div>
    <h5>This pack</h5>
    <ul>
      <li><a href="index.html">What Attest is</a></li>
      <li><a href="app.html">Close your own month</a></li>
      <li><a href="index.html#seal">How the seal works</a></li>
    </ul>
  </div>
  <div>
    <h5>Reproduce it</h5>
    <ul>
      <li><span style="color:var(--ink2)">python3 -m attest.generate --out data</span></li>
      <li><span style="color:var(--ink2)">python3 -m attest.run --data data</span></li>
      <li><span style="color:var(--ink2)">python3 -m attest.seal --verify</span></li>
    </ul>
  </div>
</div>
<div class="fbase">
  <span>Attest &middot; Razorpay AI Buildathon 2026, Track 4</span>
  <span>Synthetic benchmark corpus &middot; no real merchant data</span>
</div>
</footer>
</div></body></html>"""
    from .seal import stamp
    return stamp(doc, seal_canon)
