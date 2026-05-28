import re
import pandas as pd
from dateutil import parser as dateutil_parser
from emissions.models import (

    EmissionRecord,

    FailedRow,

    AuditLog,

)

# -----------------------------
# Column mappings
# -----------------------------

COLUMN_MAP = {
    "material": ["Material", "MATNR", "MATERIAL", "Mat", "Mat.", "MaterialCode"],
    "quantity": ["Quantity", "MENGE", "Qty", "Amount", "Volume"],
    "unit": ["Unit", "MEINS", "UOM", "Unit_of_Measure", "UoM"],
    "date": ["Date", "BUDAT", "PostingDate", "Posting_Date", "Trans_Date", "TransactionDate"],
    "document_no": ["Document_No", "BELNR", "DocumentNumber", "Doc_No", "DocNo", "Document"],
    "plant": ["Plant", "WERKS", "PlantCode", "Plant_Code", "Site"],
    "cost_center": ["Cost_Center", "KOSTL", "CostCenter", "CC"],
}

# -----------------------------
# Material normalization
# Supports substring / token matching as fallback
# -----------------------------

# Maps lowercased keyword -> canonical name.
# Keys are checked as whole-word substrings when exact lookup fails.
MATERIAL_MAP = {
    "diesel": "diesel",
    "hsd": "diesel",
    "high speed diesel": "diesel",
    "petrol": "petrol",
    "gasoline": "petrol",
    "ms fuel": "petrol",
    "motor spirit": "petrol",
    "natgas": "natgas",
    "natural gas": "natgas",
    "cng": "natgas",
    "compressed natural gas": "natgas",
    "lpg": "natgas",
    "liquefied petroleum gas": "natgas",
}

EMISSION_FACTORS = {
    "diesel": 2.68,
    "petrol": 2.31,
    "natgas": 2.04,
}

# -----------------------------
# Unit normalization → liters
# -----------------------------

UNIT_CONVERSIONS = {
    "l": 1,
    "lt": 1,
    "ltr": 1,
    "ltrs": 1,
    "liter": 1,
    "liters": 1,
    "litre": 1,
    "litres": 1,
    "gallon": 3.785,
    "gallons": 3.785,
    "gal": 3.785,
    "gals": 3.785,
    "kg": 1,       
    "kgs": 1,
    "kilogram": 1,
    "kilograms": 1,
    "scm": 1,      
    "m3": 1,
    "cubic meter": 1,
    "cubic meters": 1,
}

# -----------------------------
# Optional lookup tables
# -----------------------------

PLANT_LOOKUP = {
    "PL01": "Mumbai Refinery",
    "PL02": "Delhi Terminal",
    "WERK01": "Chennai Site",
}

CO2_FLAG_THRESHOLD = 50_000  # kg

# ==============================
# Internal helpers
# ==============================

def _normalize_header(name: str) -> str:
    """Strip BOM, leading/trailing whitespace, and normalize internal spaces."""
    return str(name).lstrip("\ufeff").strip()


def _build_column_index(df_columns) -> dict:
    """
    Return a dict: canonical_alias (original case) -> normalized-header string.
    Enables O(1) lookup after stripping headers once.
    """
    return {_normalize_header(col): col for col in df_columns}


def get_column(row, aliases, normalized_index: dict):
    """
    Return the first non-null value found for any alias,
    after stripping whitespace/BOM from the alias name.
    `normalized_index` maps cleaned header -> original header.
    """
    for alias in aliases:
        clean = _normalize_header(alias)
        original = normalized_index.get(clean)
        if original is not None and pd.notna(row.get(original)):
            return row[original]
    raise ValueError(f"Missing required column. Tried: {aliases}")


def get_column_optional(row, aliases, normalized_index: dict):
    """Like get_column but returns None instead of raising."""
    try:
        return get_column(row, aliases, normalized_index)
    except ValueError:
        return None


def normalize_material(value: str) -> str:
    """
    1. Exact lowercase match.
    2. Whole-word substring match (e.g. "Diesel Fuel (SAP)" → "diesel").
    3. Raise if still not found.
    """
    cleaned = str(value).strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)   # collapse whitespace

    # Exact match first
    if cleaned in MATERIAL_MAP:
        return MATERIAL_MAP[cleaned]

    # Substring token match — longest key wins to avoid partial false hits
    matched_key = None
    for key in sorted(MATERIAL_MAP, key=len, reverse=True):
        # match key as a whole-word sequence inside the value
        pattern = r"(?<![a-z])" + re.escape(key) + r"(?![a-z])"
        if re.search(pattern, cleaned):
            matched_key = key
            break

    if matched_key:
        return MATERIAL_MAP[matched_key]

    raise ValueError(f"Unknown material: '{value}'")


def clean_numeric(value) -> float:
    """
    Parse messy numeric strings:
      "1,200.50" → 1200.50
      "1 200,50" → 1200.50  (European style)
      " -300 L"  → strip non-numeric suffixes, return -300.0
    """
    raw = str(value).strip()

    # Remove any alphabetic suffix (unit accidentally merged into quantity)
    raw = re.sub(r"[a-zA-Z°]+$", "", raw).strip()

    # Detect European format: digits with dots as thousands sep, comma as decimal
    # Pattern: e.g. "1.200,50" or "1.200"
    if re.match(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$", raw):
        raw = raw.replace(".", "").replace(",", ".")
    else:
        # Standard: remove commas used as thousands separator
        raw = raw.replace(",", "")

    try:
        result = float(raw)
    except ValueError:
        raise ValueError(f"Cannot parse quantity: '{value}'")

    if result < 0:
        raise ValueError(f"Negative quantity not allowed: {result}")

    return result


def normalize_unit(unit_value: str) -> tuple[float, str]:
    """
    Return (multiplier, canonical_unit_string).
    Tolerates trailing dots, mixed case, plural forms.
    """
    cleaned = str(unit_value).strip().lower().rstrip(".")
    cleaned = re.sub(r"\s+", "", cleaned)   # remove internal spaces

    multiplier = UNIT_CONVERSIONS.get(cleaned)
    if multiplier is None:
        # Try stripping trailing 's' for naive plural handling
        singular = cleaned.rstrip("s")
        multiplier = UNIT_CONVERSIONS.get(singular)

    if multiplier is None:
        raise ValueError(f"Unknown unit: '{unit_value}'")

    return multiplier, cleaned


def normalize_quantity(raw_quantity, raw_unit: str) -> tuple[float, str]:
    qty = clean_numeric(raw_quantity)
    multiplier, canonical_unit = normalize_unit(raw_unit)
    return qty * multiplier, canonical_unit


def parse_date(value):
    """
    Parse dates aggressively using dateutil.
    Handles: "2024-01-15", "15/01/2024", "01-JAN-2024", "20240115", etc.
    """
    raw = str(value).strip()

    # Handle compact SAP numeric format: YYYYMMDD
    if re.match(r"^\d{8}$", raw):
        try:
            return pd.to_datetime(value,errors="raise",dayfirst=True).date()
        except Exception:
            pass

    try:
        return dateutil_parser.parse(raw, dayfirst=True).date()
    except Exception:
        raise ValueError(f"Invalid date: '{value}'")


def _safe_source_id(row, normalized_index: dict, upload_id, index: int) -> str:
    """
    Try all document_no aliases before falling back to upload+index.
    """
    val = get_column_optional(row, COLUMN_MAP["document_no"], normalized_index)
    if val is not None:
        return str(val).strip()
    return f"{upload_id}_{index}"


# ==============================
# Main parser
# ==============================

def parse_sap(upload, company, file_path):
    # --- Read CSV robustly ---
    # Try UTF-8 with BOM first, fall back to latin-1 for legacy SAP exports
    try:
        df = pd.read_csv(
            file_path,
            encoding="utf-8-sig",   # strips BOM automatically
            skipinitialspace=True,  # "  Quantity" → "Quantity"
            dtype=str,              # read everything as string; we parse types ourselves
        )
    except UnicodeDecodeError:
        df = pd.read_csv(
            file_path,
            encoding="latin-1",
            skipinitialspace=True,
            dtype=str,
        )

    # Strip whitespace from all column names (handles "  MATNR " etc.)
    df.columns = [_normalize_header(c) for c in df.columns]

    # Strip leading/trailing whitespace from all cell values
    df = df.apply(lambda col: col.str.strip() if col.dtype == object else col)

    # Replace empty strings and common null-like placeholders with NaN
    null_placeholders = {"", "NA", "N/A", "NULL", "NONE", "-", "–", "#N/A"}
    df.replace(null_placeholders, pd.NA, inplace=True)

    # Build a clean header index once (maps cleaned-header → cleaned-header,
    # since we already normalized df.columns above)
    normalized_index = {col: col for col in df.columns}

    processed = 0
    failed = 0

    for index, row in df.iterrows():
        try:
            notes = []

            # --- Required fields ---
            raw_material = get_column(row, COLUMN_MAP["material"], normalized_index)
            raw_quantity = get_column(row, COLUMN_MAP["quantity"], normalized_index)
            raw_unit = get_column(row, COLUMN_MAP["unit"], normalized_index)
            raw_date = get_column(row, COLUMN_MAP["date"], normalized_index)

            # --- Normalize ---
            material = normalize_material(raw_material)
            normalized_qty, normalized_unit = normalize_quantity(raw_quantity, raw_unit)
            date = parse_date(raw_date)

            # --- Emissions calculation ---
            factor = EMISSION_FACTORS[material]
            co2 = normalized_qty * factor

            # --- Optional: plant ---
            plant = None
            raw_plant = get_column_optional(row, COLUMN_MAP["plant"], normalized_index)
            if raw_plant is not None:
                plant = PLANT_LOOKUP.get(str(raw_plant).strip())
                if not plant:
                    notes.append(f"Unknown plant code: {raw_plant}")
            else:
                notes.append("Plant missing")

            # --- Status & flag ---
            status = "PENDING"
            flag_reason = None
            if co2 > CO2_FLAG_THRESHOLD:
                status = "FLAGGED"
                flag_reason = "CO2 exceeds threshold"

            # --- Duplicate check ---
            source_id = _safe_source_id(row, normalized_index, upload.id, index)
            if EmissionRecord.objects.filter(upload=upload, source_row_id=source_id).exists():
                notes.append("Duplicate row skipped")
                continue

            # --- Persist ---
            # create JSON-safe raw data

            safe_raw_data = {}

            for k, v in row.to_dict().items():

                if pd.isna(v):
                    safe_raw_data[k] = None

                else:
                    safe_raw_data[k] = str(v)


            # --- Persist ---
            EmissionRecord.objects.create(
                company=company,
                upload=upload,
                source_type=upload.source_type,
                scope="SCOPE_1",
                category=material,

                raw_data=safe_raw_data,

                activity_value=normalized_qty,
                activity_unit=normalized_unit,

                raw_quantity=str(raw_quantity),
                raw_unit=str(raw_unit),

                normalization_notes=notes,

                co2e_kg=co2,

                period_start=date,
                period_end=date,

                review_status=status,

                source_row_id=source_id,

                flag_reason=flag_reason,
            )
            processed +=1
        except Exception as e:

            safe_row = {}

            for k, v in row.to_dict().items():

                if pd.isna(v):
                    safe_row[k] = None

                else:
                    safe_row[k] = str(v)

            FailedRow.objects.create(
                upload=upload,
                row_number=index + 1,
                error_message=str(e),
                raw_content=safe_row,
            )

            failed += 1

    upload.rows_processed = processed
    upload.rows_failed = failed
    upload.status = "PARTIAL_SUCCESS" if failed > 0 else "COMPLETED"
    upload.save()