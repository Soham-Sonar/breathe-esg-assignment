import re
import pdfplumber
import pandas as pd

from emissions.models import (

    EmissionRecord,

    FailedRow,

    AuditLog,

)

GRID_FACTOR = 0.82

# Priority-ordered patterns: most specific labels first, generic fallback last.
# The first match wins, so "Total Consumption 1,200 kWh" beats a stray "1200 kWh"
# elsewhere on the same page.
ENERGY_PATTERNS = [
    (r'Total\s+Consumption[^0-9]*(\d[\d,\.]*)\s*(KWH|MWH|GWH)', 10),
    (r'Units\s+Consumed[^0-9]*(\d[\d,\.]*)\s*(KWH|MWH|GWH)',    10),
    (r'Energy\s+Usage[^0-9]*(\d[\d,\.]*)\s*(KWH|MWH|GWH)',       9),
    (r'Consumption[^0-9]*(\d[\d,\.]*)\s*(KWH|MWH|GWH)',          8),
    (r'NRG_USG.*?(\d[\d,\.]*)\s*(KWH|MWH|GWH)',                  7),
    (r'(\d[\d,\.]{2,})\s*(KWH|MWH|GWH)',                         1),  # generic fallback
]


# --------------------------------
# Helpers
# --------------------------------

def parse_date(value):

    value = str(value).strip()

    formats = [
        "%d/%m/%Y",
        "%d/%m/%y",
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d.%m.%Y",
        "%b %d, %Y",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)

            # Fix 2-digit years becoming year 0018/0008
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)

            return dt.date()

        except Exception:
            continue

    try:
        dt = pd.to_datetime(
            value,
            errors="raise",
            dayfirst=True
        )

        if dt.year < 100:
            dt = dt.replace(year=2000 + dt.year)

        return dt.date()

    except Exception:
        return None


def extract_dates(text):
    patterns = [
        r'\d{2}-[A-Za-z]{3}-\d{4}',
        r'\d{2}/\d{2}/\d{2,4}',
        r'\d{4}-\d{2}-\d{2}',
        r'\d{2}\.\d{2}\.\d{4}',
        r'[A-Za-z]{3}\s+\d{1,2},\s+\d{4}',
    ]
    found = []
    for p in patterns:
        found.extend(re.findall(p, text))

    parsed = []
    for f in found:
        d = parse_date(f)
        if d:
            parsed.append(d)
    return parsed


def extract_meter(text):
    patterns = [
        r'(MTR[#A-Z0-9\-_]+)',
        r'Meter\s*(?:ID|No|Ref)?[: ]+([A-Z0-9\-_]+)',
        r'Consumer Meter ID[: ]+([A-Z0-9\-_]+)',
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(1)
    return None


def normalize_quantity(quantity, unit):
    """Normalize a raw quantity string to kWh as a float."""
    cleaned = (
        str(quantity)
        .replace(",", "")
        .replace("_", "")
        .replace(" ", "")
        .replace("S", "5")   # common OCR artifact
    )
    value = float(cleaned)
    unit = unit.upper()
    if unit == "MWH":
        value *= 1_000
    elif unit == "GWH":
        value *= 1_000_000
    return value


def extract_best_energy_value(text):
    """
    Return (kwh, raw_unit, confidence_penalty, notes) using the highest-priority
    pattern that produces a match.  Unlike the original max() approach, we trust
    the label hierarchy rather than the largest number, which avoids picking up
    running-total or cumulative figures that happen to be bigger.

    Returns None for kwh if nothing is found.
    """
    best_priority = -1
    best_value = None
    best_unit = None
    notes = []

    for pattern, priority in ENERGY_PATTERNS:
        matches = re.findall(pattern, text, re.I)
        if not matches:
            continue

        candidates = []
        for qty, unit in matches:
            try:
                v = normalize_quantity(qty, unit)
                if v > 100:          # sanity floor – ignore sub-100 kWh fragments
                    candidates.append((v, unit.upper()))
            except (ValueError, TypeError):
                continue

        if not candidates:
            continue

        if priority > best_priority:
            best_priority = priority
            # If multiple values at the same priority (e.g. two "Consumption" lines),
            # flag it rather than silently picking one.
            unique_vals = {v for v, _ in candidates}
            if len(unique_vals) > 1:
                notes.append("Multiple values at same label priority – took first")
            best_value, best_unit = candidates[0]

    return best_value, best_unit, notes


# --------------------------------
# Bill-boundary detection
# --------------------------------

# Many utilities print "INVOICE" or "STATEMENT" in the header of every page of a
# multi-page bill.  The original code would split those pages into separate bills,
# each missing most of the data.
#
# Strategy: treat a new bill boundary only when we see a boundary keyword AND
# either (a) there's already accumulated content, or (b) we're on page 0.
# We also require the keyword to appear near the top of the page (first 20 lines)
# so footer repetitions don't trigger splits.

BOUNDARY_KEYWORDS = {"INVOICE", "STATEMENT", "BILL SUMMARY", "ELECTRICITY BILL"}


def is_bill_start(page_text, accumulated_pages):
    """True if this page looks like the start of a new bill."""
    top_lines = "\n".join(page_text.splitlines()[:20]).upper()
    has_keyword = any(kw in top_lines for kw in BOUNDARY_KEYWORDS)
    # Only start a new bill if we already have content (avoids splitting on page 1
    # of a multi-page bill that happens to repeat the header).
    return has_keyword and len(accumulated_pages) > 0


# --------------------------------
# Confidence scoring
# --------------------------------

def compute_confidence(energy_notes, dates, meter, kwh):
    """
    Score 0-1 based on how many signals are present and clean.
    Each missing/suspect signal subtracts from a base of 1.0.
    """
    score = 1.0

    if energy_notes:            # conflicting / uncertain energy values
        score -= 0.35

    if len(dates) < 2:          # can't establish billing period
        score -= 0.20

    if not meter:               # no meter ID found
        score -= 0.10

    if kwh and kwh > 100_000:   # unusually large – flag but don't zero out
        score -= 0.10

    return max(round(score, 2), 0.0)


# --------------------------------
# Parse Single Bill
# --------------------------------

def parse_single_bill(text, upload, company, idx):
    notes = []
    status = "PENDING"
    flag_reason = None

    # --- Energy value ---
    kwh, raw_unit, energy_notes = extract_best_energy_value(text)
    notes.extend(energy_notes)

    if kwh is None:
        raise ValueError("No energy values found")

    if energy_notes:
        status = "FLAGGED"
        flag_reason = "Multiple consumption values at same label priority"

    # --- Dates ---
    dates = extract_dates(text)
    start = None
    end = None

    if len(dates) >= 2:
        start = min(dates)
        end = max(dates)
    else:
        notes.append("Billing period unclear")

    # period_start is non-nullable; fail explicitly rather than letting the DB
    # raise an IntegrityError with a cryptic message.
    if start is None:
        raise ValueError("Could not determine billing period start date")

    # --- Meter ---
    meter = extract_meter(text)
    if not meter:
        notes.append("Missing meter ID")

    # --- Large-usage flag ---
    if kwh > 100_000:
        if status != "FLAGGED":
            status = "FLAGGED"
        flag_reason = flag_reason or "Large electricity usage"

    # --- Confidence ---
    confidence = compute_confidence(energy_notes, dates, meter, kwh)

    # --- Emissions ---
    co2 = kwh * GRID_FACTOR

    EmissionRecord.objects.create(
        company=company,
        upload=upload,
        source_type="UTILITY",
        scope="SCOPE_2",
        category="Electricity",
        raw_data={
            "kwh": kwh,
            "raw_unit": raw_unit,          # preserve original unit for audit
            "text": text[:5000],
        },
        raw_quantity=str(kwh),
        raw_unit=raw_unit or "KWH",        # actual unit, not "mixed"
        activity_value=kwh,
        activity_unit="kWh",
        normalization_notes=notes,
        confidence_score=confidence,
        parser_version="pdf_v4",
        co2e_kg=co2,
        period_start=start,
        period_end=end,
        review_status=status,
        source_row_id=f"UTILITY-{idx + 1:04d}",
        flag_reason=flag_reason,
    )


# --------------------------------
# Main parser
# --------------------------------

def parse_utility(upload, company, file_path):
    processed = 0
    failed = 0

    pages = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)

    # Group pages into per-bill chunks using improved boundary detection.
    bill_chunks = []
    current = []

    for page_text in pages:
        if is_bill_start(page_text, current):
            # Flush the accumulated bill before starting a new one.
            bill_chunks.append("\n".join(current))
            current = [page_text]
        else:
            current.append(page_text)

    if current:
        bill_chunks.append("\n".join(current))

    for idx, bill_text in enumerate(bill_chunks):
        try:
            parse_single_bill(bill_text, upload, company, idx)
            processed += 1
        except Exception as e:
            FailedRow.objects.create(
                upload=upload,
                row_number=idx + 1,
                error_message=str(e),
                raw_content={"text": bill_text[:2000]},
            )
            failed += 1

    upload.rows_processed = processed
    upload.rows_failed = failed
    upload.status = "PARTIAL_SUCCESS" if failed else "COMPLETED"
    upload.save()