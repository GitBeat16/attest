"""Readiness gate: never close on data you could not fully read. ROADMAP §1.2.

`attest/ingest.py` used to parse exactly one schema — the generator's. A real
Razorpay or bank export broke it in one of two ways: an uncaught traceback that
named nothing, or a silent coercion into a valid-looking record. Neither is a
close that can be trusted, and a clean-looking result over 80% of the rows is
the single most dangerous outcome this product has.

Every test here asserts the *verdict and the reason*, not merely that something
was raised. The negative cases matter just as much: the generated corpus must
still read READY, and no fixture may ever produce a guessed column.

Runs under pytest, and standalone:  python3 tests/test_ingest_readiness.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attest.ingest import load                                    # noqa: E402
from attest.readiness import (MISSING, SPECS, parse_money,         # noqa: E402
                              read_table, resolve_date_parser)

FIX = Path(__file__).resolve().parent / "fixtures" / "ingest"
SETL = SPECS["razorpay_settlements.csv"]
BANK = SPECS["bank_statement.csv"]


def _read(name, spec=SETL, declared=None):
    return read_table(FIX / name, spec, declared)


def _corpus(settlement_fixture, *, period="2026-08", orders=None, terms_extra=None):
    """A minimal data dir with one fixture as the settlement report."""
    d = Path(tempfile.mkdtemp()) / "sources"
    d.mkdir(parents=True)
    (d / "razorpay_settlements.csv").write_text(
        (FIX / settlement_fixture).read_text(encoding="utf-8"), encoding="utf-8")
    (d / "razorpay_mdr_invoice.json").write_text(
        json.dumps({"period": period, "total_tax": ""}))
    terms = {"merchant": "Test", "period": period,
             "contracted_mdr_rate_pct": "2.00", "gst_on_mdr_rate_pct": "18.00",
             "courier_cod_fee_pct": "1.50", "courier_rto_freight_inr": "85.00"}
    terms.update(terms_extra or {})
    (d / "contract_terms.json").write_text(json.dumps(terms))
    if orders:
        (d / "orders.csv").write_text(orders)
    return load(d)


# =====================================================================
# parse_money — the shapes a real export actually uses
# =====================================================================
def test_indian_digit_grouping_parses():
    assert parse_money("1,23,456.78") == (12345678, None)


def test_western_digit_grouping_parses():
    assert parse_money("1,234.00") == (123400, None)


def test_rupee_sign_and_grouping_together():
    assert parse_money("₹1,234.50") == (123450, None)


def test_accounting_parenthesis_is_negative():
    assert parse_money("(50.00)") == (-5000, None)


def test_cr_suffix_is_positive_dr_suffix_is_negative():
    assert parse_money("1000.00 Cr") == (100000, None)
    assert parse_money("1234.00 Dr") == (-123400, None)


def test_scientific_notation_is_rejected_not_accepted():
    v, why = parse_money("1.2345e3")
    assert v is None and "scientific notation" in why


def test_sub_paise_is_rejected_not_rounded():
    v, why = parse_money("100.005")
    assert v is None and "decimal places" in why


def test_lone_comma_is_rejected_as_ambiguous():
    v, why = parse_money("12,5")
    assert v is None and "ambiguous" in why


def test_blank_amount_is_MISSING_not_zero():
    assert parse_money("") == (MISSING, None)
    assert parse_money("   ") == (MISSING, None)
    assert parse_money("0.00") == (0, None)   # a real zero is a value


# =====================================================================
# resolve_date_parser — one format per column, decided over the column
# =====================================================================
def test_iso_dates_read_without_a_declaration():
    p, note = resolve_date_parser(["2026-08-01", "2026-08-31"])
    assert p and "ISO" in note


def test_ddmmyyyy_is_used_when_a_row_proves_it():
    p, note = resolve_date_parser(["25/08/2026", "04/08/2026"])
    assert p and note == "DD/MM/YYYY"


def test_ambiguous_numeric_dates_refuse_without_a_declaration():
    p, why = resolve_date_parser(["08/04/2026", "09/04/2026"])
    assert p is None and "date_format" in why


def test_ambiguous_numeric_dates_accept_an_explicit_declaration():
    p, note = resolve_date_parser(["08/04/2026", "09/04/2026"],
                                  declared_format="DD/MM/YYYY")
    assert p and note == "DD/MM/YYYY"


def test_excel_serial_dates_refuse():
    p, why = resolve_date_parser(["46234", "46250"])
    assert p is None and "serial" in why


# =====================================================================
# read_table — reject, never coerce; recognise, never guess
# =====================================================================
def test_a_bom_on_the_header_does_not_break_the_read():
    rows, sr = _read("bom_header.csv")
    assert sr.bom and sr.rows_read == sr.rows_in == 3 and not sr.rejections


def test_real_razorpay_headers_map_through_the_alias_table():
    rows, sr = _read("razorpay_real_headers.csv")
    assert not sr.rejections and not sr.columns_missing
    assert sr.columns_recognised["gross"] == "amount"
    assert sr.columns_recognised["mdr"] == "fee"
    assert sr.columns_recognised["net"] == "credit"
    assert sr.columns_recognised["row_type"] == "type"


def test_an_unrecognised_column_is_reported_never_guessed():
    rows, sr = _read("unknown_column.csv")
    assert sr.columns_unrecognised == ["promo_code"]


def test_a_ragged_row_is_rejected_with_the_field_counts():
    rows, sr = _read("ragged_row.csv")
    assert sr.rows_read == sr.rows_in - 1
    assert any("ragged" in r.reason for r in sr.rejections)


def test_an_unknown_row_type_value_is_a_rejected_row_not_a_dropped_one():
    rows, sr = _read("unknown_row_type.csv")
    assert sr.rows_read == sr.rows_in - 1
    assert any(r.column == "row_type" for r in sr.rejections)


def test_semicolon_delimited_is_read():
    rows, sr = _read("semicolon.csv")
    assert sr.delimiter == ";" and sr.rows_read == 3 and not sr.rejections


def test_a_bank_preamble_is_skipped_and_reported():
    rows, sr = _read("preamble_bank.csv", BANK)
    assert sr.preamble_skipped == 4 and sr.rows_read == 1


def test_debit_rows_are_read_and_the_trailer_is_rejected():
    rows, sr = _read("debit_rows_bank.csv", BANK)
    # 5 physical rows: 2 credits + 2 debits read, the "Closing Balance" trailer
    # rejected — never coerced into a ₹0 credit.
    assert sr.rows_read == 4
    assert any("Closing Balance" in r.raw for r in sr.rejections)


# =====================================================================
# load() — the verdict the whole close hangs on
# =====================================================================
def test_the_generated_corpus_reads_READY():
    c = load(Path(__file__).resolve().parent.parent / "data" / "sources")
    assert c.readiness.ready, c.readiness.reasons
    assert c.readiness.rows_rejected() == 0
    # every supplied column was recognised — nothing guessed anywhere
    for s in c.readiness.sources.values():
        assert not s.columns_unrecognised, (s.name, s.columns_unrecognised)


def test_a_quoted_indian_grouped_amount_is_read_not_refused():
    c = _corpus("indian_grouping.csv")
    assert not c.readiness.refused
    assert not c.readiness.sources["razorpay_settlements.csv"].rejections


def test_an_ambiguous_amount_in_the_settlement_report_REFUSES():
    c = _corpus("euro_decimal_comma.csv")
    assert c.readiness.refused
    assert any("ambiguous" in r for r in c.readiness.reasons)


def test_ambiguous_dates_refuse_but_a_declared_format_lets_the_close_run():
    refused = _corpus("ambiguous_dates.csv")
    assert refused.readiness.refused
    ok = _corpus("ambiguous_dates.csv", period="2026-04",
                 terms_extra={"date_format": "DD/MM/YYYY"})
    assert not ok.readiness.refused


def test_a_half_month_of_settlements_is_PARTIAL_not_READY():
    c = _corpus("half_month_settlements.csv")
    assert c.readiness.partial
    assert any("before the month ends" in r or "partial month" in r
               for r in c.readiness.reasons)


def test_three_months_filed_as_one_REFUSES():
    c = _corpus("three_months.csv")
    assert c.readiness.refused
    assert any("span" in r and "one month" in r for r in c.readiness.reasons)


def test_a_missing_settlement_report_REFUSES():
    d = Path(tempfile.mkdtemp()) / "sources"
    d.mkdir(parents=True)
    (d / "razorpay_mdr_invoice.json").write_text(json.dumps({"period": "2026-08"}))
    (d / "contract_terms.json").write_text(json.dumps(
        {"merchant": "T", "period": "2026-08", "contracted_mdr_rate_pct": "2.00",
         "gst_on_mdr_rate_pct": "18.00", "courier_cod_fee_pct": "1.50",
         "courier_rto_freight_inr": "85.00"}))
    c = load(d)
    assert c.readiness.refused
    assert any("settlement report" in r for r in c.readiness.reasons)


def test_no_fixture_ever_produces_a_guessed_column():
    """Every recognised column is an exact alias-table hit — nothing fuzzy."""
    from attest.readiness import _norm
    for name, spec in ((f.name, BANK if "bank" in f.name else SETL)
                       for f in FIX.glob("*.csv")):
        _, sr = read_table(FIX / name, spec)
        for canon, header in sr.columns_recognised.items():
            col = spec.col(canon)
            assert _norm(header) in col.aliases, (name, canon, header)


def _run_standalone() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in fns:
        try:
            fn()
        except AssertionError as e:
            failed.append((name, str(e) or "assertion failed"))
        except Exception as e:                     # noqa: BLE001
            failed.append((name, f"{type(e).__name__}: {e}"))
    for name, why in failed:
        print(f"  FAIL  {name}\n        {why}")
    print(f"  {len(fns) - len(failed)}/{len(fns)} readiness tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
