"""Render the attestation as a single self-contained HTML file.

No server, no build step, no dependencies. The data is inlined, so the file can
be opened by double-clicking it, committed to docs/ for GitHub Pages, or dropped
into an email. That is deliberate: a finance tool a CA can actually run should
not require a runtime.

The page is an operator console, not a marketing site. It is read top to bottom
in one pass, and it is built so the eye follows one specific sequence -- the
match rate, then the proof rate beneath it, then what survived the attack.
That descent is the argument.
"""
from __future__ import annotations

import json
from datetime import datetime

from .money import fmt

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0E1116; --panel:#161A21; --panel2:#1C222B; --rule:#232935;
  --ink:#E6E9EF; --mid:#A2ABBA; --dim:#6F7889;
  --proof:#4DB6A0; --good:#5FBE8E; --warn:#E0A458; --bad:#E0664F; --cool:#6E9BD8;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  --sans:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);
     font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:36px 28px 72px}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}

header{display:flex;justify-content:space-between;align-items:flex-end;
       gap:24px;flex-wrap:wrap;border-bottom:1px solid var(--rule);padding-bottom:20px}
.brand{font-family:var(--mono);font-size:20px;letter-spacing:.24em;
       text-transform:uppercase;color:var(--proof);font-weight:600}
.sub{color:var(--mid);font-size:13px;margin-top:6px;max-width:52ch}
.meta{font-family:var(--mono);font-size:11.5px;color:var(--dim);text-align:right;
      line-height:1.9;letter-spacing:.02em}
.meta b{color:var(--mid);font-weight:400}

h2{font-family:var(--mono);font-size:11px;letter-spacing:.2em;text-transform:uppercase;
   color:var(--dim);font-weight:500;margin:40px 0 14px;
   padding-bottom:8px;border-bottom:1px solid var(--rule)}

/* the descent */
.rates{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--rule);
       border:1px solid var(--rule)}
.rate{background:var(--panel);padding:22px 24px 20px;position:relative}
.rate .lbl{font-family:var(--mono);font-size:10.5px;letter-spacing:.16em;
           text-transform:uppercase;color:var(--dim)}
.rate .val{font-family:var(--mono);font-size:40px;font-weight:600;line-height:1.05;
           margin:12px 0 4px;letter-spacing:-.02em}
.rate .frac{font-family:var(--mono);font-size:12px;color:var(--mid)}
.rate .say{font-size:12px;color:var(--dim);margin-top:12px;line-height:1.45}
.r1 .val{color:var(--mid)} .r2 .val{color:var(--proof)} .r3 .val{color:var(--bad)}
.bar{height:3px;background:var(--panel2);margin-top:14px;border-radius:2px;overflow:hidden}
.bar i{display:block;height:100%}
.r1 .bar i{background:var(--mid)} .r2 .bar i{background:var(--proof)}
.r3 .bar i{background:var(--bad)}

table{width:100%;border-collapse:collapse;font-size:13px}
th{font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;
   color:var(--dim);font-weight:500;text-align:left;padding:9px 12px;
   background:var(--panel2);border-bottom:1px solid var(--rule)}
td{padding:9px 12px;border-bottom:1px solid var(--rule);color:var(--mid);
   vertical-align:top}
tr:last-child td{border-bottom:none}
td.k{color:var(--ink)}
td.n,th.n{font-family:var(--mono);text-align:right;white-space:nowrap;
          font-variant-numeric:tabular-nums}
.tbl{border:1px solid var(--rule);background:var(--panel);overflow-x:auto}
tr.miss td{background:rgba(224,102,79,.07)}
tr.held td.k{color:var(--cool)}

.tag{font-family:var(--mono);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
     padding:2px 6px;border-radius:2px;white-space:nowrap}
.t-ok{background:rgba(95,190,142,.13);color:var(--good)}
.t-miss{background:rgba(224,102,79,.13);color:var(--bad)}
.t-held{background:rgba(110,155,216,.13);color:var(--cool)}

.note{border-left:2px solid var(--proof);background:var(--panel);padding:14px 18px;
      margin-top:12px;font-size:12.5px;color:var(--mid);line-height:1.55}
.note b{color:var(--ink);font-weight:600}

.attest{border:1px solid var(--rule);background:var(--panel);margin-top:14px;
        display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:var(--rule)}
.attest > div{background:var(--panel);padding:20px 24px}
.attest .lbl{font-family:var(--mono);font-size:10.5px;letter-spacing:.16em;
             text-transform:uppercase;color:var(--dim)}
.attest .v{font-family:var(--mono);font-size:22px;margin-top:10px;font-weight:600}
.status-no{color:var(--warn)} .status-yes{color:var(--good)}
.reason{font-size:12px;color:var(--dim);margin-top:8px;line-height:1.45}

.chain{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:12px;
       font-family:var(--mono);font-size:11px}
.node{padding:5px 10px;border:1px solid var(--rule);border-radius:3px;
      background:var(--panel2);color:var(--mid)}
.node.ok{border-color:rgba(95,190,142,.4);color:var(--good)}
.node.bad{border-color:rgba(224,102,79,.5);color:var(--bad);
          background:rgba(224,102,79,.08)}
.arrow{color:var(--dim)}
footer{margin-top:52px;padding-top:20px;border-top:1px solid var(--rule);
       font-family:var(--mono);font-size:11px;color:var(--dim);line-height:1.8}
@media(max-width:820px){.rates,.attest{grid-template-columns:1fr}}
"""


def _pc(x: float) -> str:
    return f"{x*100:.1f}%"


def render(p: dict) -> str:
    rates = f"""
<div class="rates">
  <div class="rate r1">
    <div class="lbl">Match rate</div>
    <div class="val">{_pc(p['match_rate'])}</div>
    <div class="frac">{p['tied']}/{p['batches']} batches tie to the bank</div>
    <div class="bar"><i style="width:{p['match_rate']*100:.1f}%"></i></div>
    <div class="say">What conventional reconciliation reports. Razorpay pays what
      its own report says, so this agrees by construction.</div>
  </div>
  <div class="rate r2">
    <div class="lbl">Proof rate</div>
    <div class="val">{_pc(p['proof_rate'])}</div>
    <div class="frac">{p['proven']}/{p['lines']} lines fully traced</div>
    <div class="bar"><i style="width:{p['proof_rate']*100:.1f}%"></i></div>
    <div class="say">Every link present: order &rarr; fee &rarr; tax &rarr; batch
      &rarr; bank. Break one and the line is not proven.</div>
  </div>
  <div class="rate r3">
    <div class="lbl">False-match rate</div>
    <div class="val">{_pc(p['false_match_rate'])}</div>
    <div class="frac">{p['overturned']}/{p['tested']} claims overturned</div>
    <div class="bar"><i style="width:{p['false_match_rate']*100:.1f}%"></i></div>
    <div class="say">Matches the adversarial pass broke. The number nobody
      volunteers, published on purpose.</div>
  </div>
</div>"""

    ex_rows = "".join(
        f"<tr><td class='k'>{e['class']}</td><td class='n'>{e['count']}</td>"
        f"<td class='n'>{fmt(e['exposure'])}</td>"
        f"<td>{e['evidence_required']}</td></tr>"
        for e in p["exceptions"][:12]
    )

    sc_rows = ""
    for cls, s in sorted(p["scorecard"].items()):
        held = s["held_out"]
        missed = s["detected"] == 0
        cls_attr = "miss" if (held and missed) else ("held" if held else "")
        if held:
            tag = ("<span class='tag t-miss'>held out · missed</span>" if missed
                   else "<span class='tag t-held'>held out · found</span>")
        else:
            tag = "<span class='tag t-ok'>designed for</span>"
        sc_rows += (
            f"<tr class='{cls_attr}'><td class='k'>{cls}</td>"
            f"<td class='n'>{s['planted']}</td><td class='n'>{s['detected']}</td>"
            f"<td class='n'>{s['recall']*100:.0f}%</td><td>{tag}</td></tr>"
        )

    chain = "".join(
        f"<span class='node {'bad' if n == p['chain_break'] else 'ok'}'>{n}</span>"
        + ("<span class='arrow'>&rarr;</span>" if i < len(p["chain_nodes"]) - 1 else "")
        for i, n in enumerate(p["chain_nodes"])
    )

    rc = p.get("recovery")
    recovery = ""
    if rc:
        dl_rows = "".join(
            f"<tr><td class='mono'>{d['deadline']}</td>"
            f"<td class='n'><span class='tag {'t-miss' if d['urgency'] in ('critical','lapsed') else 't-held' if d['urgency']=='urgent' else 't-ok'}'>"
            f"{d['days']}d</span></td>"
            f"<td class='k'>{d['cls']}</td><td class='n'>{fmt(d['exposure'])}</td>"
            f"<td>{d['party']}</td></tr>"
            for d in rc["deadlines"]
        )
        party = " &nbsp;·&nbsp; ".join(
            f"{k} <b style='color:var(--ink)'>{fmt(v)}</b>"
            for k, v in sorted(rc["by_counterparty"].items(), key=lambda x: -x[1])
        )
        recovery = f'''
<div class="attest" style="grid-template-columns:1fr 1fr 1fr">
  <div><div class="lbl">Recoverable</div>
    <div class="v" style="color:var(--good)">{fmt(rc['recoverable'])}</div>
    <div class="reason">{rc['recoverable_count']} items, claimable from a counterparty</div></div>
  <div><div class="lbl">Expiring within 7 days</div>
    <div class="v" style="color:var(--warn)">{fmt(rc['expiring_soon'])}</div>
    <div class="reason">{rc['expiring_count']} items whose claim window is closing</div></div>
  <div><div class="lbl">Cost of closing monthly</div>
    <div class="v" style="color:var(--bad)">{fmt(rc['monthly_lapsed'])}</div>
    <div class="reason">{rc['monthly_lapsed_count']} claims would expire unfiled if this
      close ran on {rc['late_date']} instead of {rc['as_at']}</div></div>
</div>
<div class="note"><b>Why cadence is the product.</b> Courier disputes close in
  14 days. A month-end close surfaces a day-8 finding on day 31, by which point
  the money is gone and the report is an obituary. Running daily is not a
  performance detail &mdash; it is the difference between finding money and
  recovering it. &nbsp;{party}</div>
<div class="tbl" style="margin-top:12px"><table>
<thead><tr><th>Deadline</th><th class="n">Left</th><th>Class</th>
<th class="n">Exposure</th><th>Counterparty</th></tr></thead>
<tbody>{dl_rows}</tbody></table></div>'''

    notes = "".join(f"<div class='note'>{n}</div>" for n in p["notes"])
    signed = p["signed"]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Attest — close pack {p['period']}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{CSS}</style></head><body><div class="wrap">

<header>
  <div>
    <div class="brand">Attest</div>
    <div class="sub">Close pack for {p['merchant']}. Reports what can be
      proven, not what happened to match.</div>
  </div>
  <div class="meta">
    <b>period</b> {p['period']}<br>
    <b>records</b> {p['records']:,} from 7 sources<br>
    <b>closed in</b> {p['seconds']}s<br>
    <b>generated</b> {p['generated']}
  </div>
</header>

<h2>The three rates</h2>
{rates}

<h2>One proof chain</h2>
<div class="note" style="border-left-color:var(--bad)">
  <b>{p['chain_subject']}</b> — {p['chain_detail']}
  <div class="chain">{chain}</div>
</div>

<h2>Recovery &mdash; what is claimable, and for how much longer</h2>
{recovery}

<h2>Exception register &mdash; ranked by rupee exposure</h2>
<div class="tbl"><table>
<thead><tr><th>Class</th><th class="n">Count</th><th class="n">Exposure</th>
<th>Evidence required to close it</th></tr></thead>
<tbody>{ex_rows}</tbody></table></div>

<h2>Accuracy against held-out ground truth</h2>
<div class="tbl"><table>
<thead><tr><th>Defect class</th><th class="n">Planted</th><th class="n">Found</th>
<th class="n">Recall</th><th>Provenance</th></tr></thead>
<tbody>{sc_rows}</tbody></table></div>
<div class="note">
  <b>Why these numbers can be trusted.</b> The corpus is generated truth-first:
  the true ledger is built and frozen, documents are derived from it, and defects
  are injected into the documents only. The labels are never read by the pipeline.
  Four classes were planted with <b>no detector written for them</b> — recall on
  those is {_pc(p['holdout_recall'])}, and the class still missed is shown in red.
  Designed-for recall is {_pc(p['designed_recall'])}, which on its own would prove
  very little.
</div>
{notes}

<h2>Attestation</h2>
<div class="attest">
  <div><div class="lbl">Period volume</div>
       <div class="v">{fmt(p['volume'])}</div></div>
  <div><div class="lbl">Unexplained residual</div>
       <div class="v">{fmt(p['residual_paise'])}</div>
       <div class="reason">{p['residual_bps']} bps of volume</div></div>
  <div><div class="lbl">Close status</div>
       <div class="v {'status-yes' if signed else 'status-no'}">
         {'SIGNED' if signed else 'NOT ATTESTABLE'}</div>
       <div class="reason">{'Residual within limit; every line traced or typed.'
         if signed else
         'Residual exceeds the 25 bps limit. Refusing to certify is the point of '
         'an attestation — the close stays open until this is investigated.'}</div>
  </div>
</div>

<footer>
  Attest &middot; Razorpay AI Buildathon, Track 4 &middot; synthetic corpus, no real
  merchant data<br>
  Rules engine, zero runtime dependencies. Every figure reproducible from the
  seeded corpus.
</footer>
</div></body></html>"""
