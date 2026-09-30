#!/usr/bin/env python3
"""Measure extraction precision/recall against the golden set.

Computes per-field precision/recall/F1 for the mandatory extraction fields,
plus an "auto-confirm" gate over the subset of predictions the extractor
marked high-confidence. Exits non-zero if any configured gate fails.

Usage:
    python scripts/measure_extraction.py \\
        --ground-truth extraction/golden_set/ground_truth \\
        --predictions extraction/golden_set/predictions \\
        [--thresholds extraction/golden_set/thresholds.json]

File naming: one JSON file per document, both keyed by doc_id, e.g.
    ground_truth/INV-0001.json  -> {"doc_id": "INV-0001", "capture_source": "...", "fields": {...}}
    predictions/INV-0001.json   -> {"doc_id": "INV-0001", "fields": {...}, "confidence": {...}}

Gates (see docs/EXTRACTION_SPEC.md §4):
    G1 mandatory precision >= 0.90
    G2 mandatory recall    >= 0.95
    G3 auto-confirm precision >= 0.99 (over the high-confidence subset)

DEBUGGING QUICK MAP
-------------------
  Exit 2  → empty ground_truth/ or predictions/ dir (no *.json)
  Exit 1  → printed "=== Gates ===" section has at least one FAIL
  Exit 0  → all active gates PASS

  Per-field table: low recall on a field → missing predictions (pv is None) or wrong values.
  Auto-confirm n/a → no doc passed doc_eligible (confidence too low on any GT field).

  Compare logic lives in field_equal() + norm_* helpers — breakpoint there for mismatches.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Field taxonomy: name -> comparison kind.
MANDATORY_FIELDS = {
    "supplier_gstin": "gstin",
    "buyer_gstin": "gstin",
    "invoice_no": "string",
    "invoice_date": "date",
    "place_of_supply": "string",
    "is_inter_state": "bool",
    "rchrg": "bool",
    "inv_typ": "enum",
    "taxable_value_paise": "paise",
    "total_value_paise": "paise",
    "cgst_paise": "paise",
    "sgst_paise": "paise",
    "igst_paise": "paise",
    "cess_paise": "paise",
}

DEFAULT_THRESHOLDS = {
    "mandatory_precision": 0.90,
    "mandatory_recall": 0.95,
    "auto_confirm_precision": 0.99,
}

# Auto-confirm confidence threshold per capture source (docs/EXTRACTION_SPEC.md §5).
AUTO_CONFIRM_CONFIDENCE = {
    "PDF_SCAN": 0.90,
    "DIGITAL": 0.90,
    "PHOTO": 0.97,
    "WHATSAPP": 0.97,
}

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{2}$")


def gstin_checksum_valid(g):
    """Validate a 15-char GSTIN by structure + mod-36 checksum digit.

    Flow:
        1. Uppercase and strip spaces from input.
        2. Reject if regex _GSTIN_RE does not match (15-char GSTIN shape).
        3. For chars 0..13: map to base-36 value, multiply by alternating weight 1/2,
           add (prod//36 + prod%36) to running total (GSTN checksum algorithm).
        4. Compare (total % 36) to base-36 index of the 15th checksum character.

    Returns:
        True if structurally valid and checksum digit matches; False otherwise.

    Debug:
        - False at step 2 → format issue (wrong length/charset), not checksum math.
        - False at step 4 → valid format but wrong check digit; use tests/gstin_fixtures.py
          for the complement form used in production code (this helper is harness-local).
    """
    g = (g or "").upper().replace(" ", "")
    if not _GSTIN_RE.fullmatch(g):
        return False
    weights = [1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2]
    total = 0
    for i, ch in enumerate(g[:14]):
        v = _CHARSET.index(ch)
        prod = v * weights[i]
        total += (prod // 36) + (prod % 36)
    return (total % 36) == _CHARSET.index(g[14])


def norm_string(s):
    """Normalize free-text fields for case/punctuation-insensitive comparison.

    Flow:
        1. None → None (caller treats dual-None as equal in field_equal).
        2. Uppercase and keep only alphanumeric characters (drops spaces, dashes, etc.).

    Debug:
        If two invoice numbers look equal but field_equal fails, print norm_string(a/b)
        to see hidden Unicode or punctuation differences.
    """
    if s is None:
        return None
    return "".join(c for c in str(s).upper() if c.isalnum())


def norm_date(d):
    """Normalize date strings to ISO YYYY-MM-DD when parseable.

    Flow:
        1. None → None.
        2. Split on / - . and drop empty parts; need exactly 3 parts or fall back to upper strip.
        3. If first part has length 4 → assume Y-M-D order.
        4. Else assume D-M-Y (common on Indian invoices) and reorder to Y-M-D with zero-pad.

    Debug:
        Unparseable strings return s.upper() unchanged — mismatches between GT and pred
        formats (e.g. "04-05-2025" vs "2025-05-04") should still match after step 3/4;
        if not, check which branch was taken (len(parts[0]) == 4).
    """
    if d is None:
        return None
    s = str(d).strip()
    parts = [p for p in re.split(r"[/\-.]", s) if p]
    if len(parts) != 3:
        return s.upper()
    if len(parts[0]) == 4:
        y, m, dd = parts
        return f"{y}-{m.zfill(2)}-{dd.zfill(2)}"
    dd, m, y = parts
    return f"{y}-{m.zfill(2)}-{dd.zfill(2)}"


def field_equal(kind, a, b):
    """Compare ground-truth value `a` to prediction `b` using field-type rules.

    Flow:
        1. If either side is None → equal only when both are None (missing vs value ≠).
        2. Dispatch on `kind`:
           - gstin/string → norm_string equality
           - date → norm_date equality
           - bool → bool() coercion
           - enum → strip/upper string match
           - paise → int() equality (TypeError/ValueError → False)
        3. Unknown kind → plain str equality (should not occur for MANDATORY_FIELDS).

    Debug:
        Set a breakpoint when gv is not None and not ok in main()'s doc loop;
        log kind, a, b, and normalized forms. Most filing errors are paise int drift
        or GSTIN normalization.
    """
    if a is None or b is None:
        return (a is None) and (b is None)
    if kind in ("gstin", "string"):
        return norm_string(a) == norm_string(b)
    if kind == "date":
        return norm_date(a) == norm_date(b)
    if kind == "bool":
        return bool(a) == bool(b)
    if kind == "enum":
        return str(a).strip().upper() == str(b).strip().upper()
    if kind == "paise":
        try:
            return int(a) == int(b)
        except (TypeError, ValueError):
            return False
    return str(a) == str(b)


def load_json_dir(d):
    """Load all *.json files in directory `d` into a dict keyed by doc_id.

    Flow:
        1. Sorted glob of *.json (deterministic iteration order for reproducible prints).
        2. json.load each file; index by doc["doc_id"] (KeyError if missing — surfaces bad fixture).

    Returns:
        dict[str, dict] mapping doc_id → parsed JSON object.

    Debug:
        Duplicate doc_id in two files → later file wins silently; check sorted glob order.
        Empty dict → main exits 2 with "no ground-truth/prediction files found".
    """
    out = {}
    for f in sorted(Path(d).glob("*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        out[doc["doc_id"]] = doc
    return out


def main(argv=None):
    """CLI entry: score predictions vs ground truth and enforce G1/G2/G3 gates.

    Flow:
        1. Parse CLI: --ground-truth, --predictions, optional --thresholds JSON overlay.
        2. Merge thresholds with DEFAULT_THRESHOLDS.
        3. load_json_dir both sides; abort exit 2 if either side empty.
        4. Compute doc_id sets:
           - doc_ids = intersection (only scored pairs)
           - missing_gt / missing_pred → WARN only (do not fail gates)
        5. For each doc_id in intersection:
           a. Read gf/pf from fields sub-object (or root legacy shape).
           b. Read confidence map and capture_source from ground truth.
           c. For each mandatory field: update tp/pred/gt counts; track doc_has_error.
           d. doc_eligible for G3 if every GT-present field has pred + conf >= source threshold.
           e. If eligible, increment auto_docs; if eligible and doc_has_error, auto_errors++.
        6. Print per-field prec/rec/F1 table and aggregate mandatory prec/rec.
        7. Print auto-confirm stats (precision = (auto_docs - auto_errors) / auto_docs).
        8. Evaluate gates: mandatory_precision, mandatory_recall, optional auto_confirm_precision.
        9. Return 1 if any gate FAIL else 0.

    Returns:
        int exit code for sys.exit (0 pass, 1 gate fail, 2 input error).

    Debug:
        - G2 recall fail, high precision → many pv is None (extractor omitted fields).
        - G1 precision fail → wrong values where pv is not None.
        - G3 skipped message → auto_docs == 0; lower conf in predictions or missing confidence keys.
        - Intersection empty but both dirs non-empty → doc_id mismatch between GT and pred filenames/JSON.
    """
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ground-truth", required=True)
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--thresholds", default=None)
    args = ap.parse_args(argv)

    thresholds = dict(DEFAULT_THRESHOLDS)
    if args.thresholds:
        with open(args.thresholds, "r", encoding="utf-8") as fh:
            thresholds.update(json.load(fh))

    gt = load_json_dir(args.ground_truth)
    pred = load_json_dir(args.predictions)

    if not gt:
        print("ERROR: no ground-truth files found.", file=sys.stderr)
        return 2
    if not pred:
        print("ERROR: no prediction files found.", file=sys.stderr)
        return 2

    doc_ids = sorted(set(gt) & set(pred))
    missing_gt = sorted(set(pred) - set(gt))
    missing_pred = sorted(set(gt) - set(pred))

    field_tp = {f: 0 for f in MANDATORY_FIELDS}
    field_pred_cnt = {f: 0 for f in MANDATORY_FIELDS}
    field_gt_cnt = {f: 0 for f in MANDATORY_FIELDS}

    auto_docs = 0
    auto_errors = 0

    # ── Per-document scoring loop (micro-averages aggregated across all field instances) ──
    for did in doc_ids:
        g = gt[did]
        p = pred[did]
        gf = g.get("fields", g)
        pf = p.get("fields", p)
        conf = p.get("confidence", {})
        source = g.get("capture_source", "PDF_SCAN")
        conf_thresh = AUTO_CONFIRM_CONFIDENCE.get(source, 0.90)

        doc_eligible = True
        doc_has_error = False
        for f, kind in MANDATORY_FIELDS.items():
            gv = gf.get(f)
            pv = pf.get(f)
            if gv is not None:
                field_gt_cnt[f] += 1
            if pv is not None:
                field_pred_cnt[f] += 1
            ok = field_equal(kind, pv, gv)
            if gv is not None and pv is not None and ok:
                field_tp[f] += 1
            if gv is not None and not ok:
                doc_has_error = True
            if gv is not None and (pv is None or conf.get(f, 0.0) < conf_thresh):
                doc_eligible = False

        if doc_eligible:
            auto_docs += 1
            if doc_has_error:
                auto_errors += 1

    print("\n=== Per-field precision / recall / F1 (mandatory fields) ===")
    print(f"{'field':<20} {'prec':>6} {'rec':>6} {'f1':>6} {'tp':>4} {'pred':>5} {'gt':>5}")
    for f, kind in MANDATORY_FIELDS.items():
        tp = field_tp[f]
        pn = field_pred_cnt[f]
        gn = field_gt_cnt[f]
        prec = tp / pn if pn else 0.0
        rec = tp / gn if gn else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        print(f"{f:<20} {prec:6.3f} {rec:6.3f} {f1:6.3f} {tp:4d} {pn:5d} {gn:5d}")

    tot_tp = sum(field_tp.values())
    tot_pred = sum(field_pred_cnt.values())
    tot_gt = sum(field_gt_cnt.values())
    agg_prec = tot_tp / tot_pred if tot_pred else 0.0
    agg_rec = tot_tp / tot_gt if tot_gt else 0.0
    print(f"\nAggregate mandatory: precision={agg_prec:.3f}  recall={agg_rec:.3f}")

    auto_prec = (auto_docs - auto_errors) / auto_docs if auto_docs else None
    print(f"Auto-confirm: {auto_docs} docs eligible, {auto_errors} with errors, "
          f"precision={auto_prec if auto_prec is not None else 'n/a'}")

    if missing_gt:
        print(f"\nWARN: predictions with no ground truth ({len(missing_gt)}): {missing_gt[:5]}...")
    if missing_pred:
        print(f"WARN: ground truth with no prediction ({len(missing_pred)}): {missing_pred[:5]}...")

    print("\n=== Gates ===")
    checks = [
        ("mandatory_precision", agg_prec, thresholds["mandatory_precision"]),
        ("mandatory_recall", agg_rec, thresholds["mandatory_recall"]),
    ]
    if auto_docs:
        checks.append(("auto_confirm_precision", auto_prec, thresholds["auto_confirm_precision"]))
    else:
        print("NOTE: no auto-confirm-eligible docs; auto_confirm gate skipped")

    failed = False
    for name, val, thr in checks:
        ok = val is not None and val >= thr
        shown = f"{val:.3f}" if val is not None else "n/a"
        print(f"  {name}: {shown} (threshold {thr}) {'PASS' if ok else 'FAIL'}")
        if not ok:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
