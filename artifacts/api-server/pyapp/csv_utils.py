import csv
import io


_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(value):
    """Neutralises CSV/Excel formula injection: a cell that starts with =,
    +, -, @, or a leading tab/CR can execute as a formula when the export
    is later opened in a spreadsheet app. Prefixing with a leading
    apostrophe is the standard OWASP mitigation and is invisible in
    Excel/Sheets (the cell displays and re-exports as plain text)."""
    if value is None:
        return value
    text = str(value)
    # Strip only leading ordinary spaces before checking -- a bare
    # `.lstrip()` would also strip a leading tab/CR, which are themselves
    # two of the characters this check exists to catch.
    if text.lstrip(" ").startswith(_FORMULA_TRIGGER_CHARS):
        return "'" + text
    return text


def stringify_rows_to_csv(rows: list[dict], columns: list[str], sanitize: bool = False) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        values = {c: ("" if row.get(c) is None else row.get(c)) for c in columns}
        if sanitize:
            values = {c: sanitize_csv_cell(v) for c, v in values.items()}
        writer.writerow(values)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Controlled learner import (Phase 5) -- multipart upload, real validation.
# ---------------------------------------------------------------------------

LEARNER_IMPORT_COLUMNS = [
    "learner_reference",
    "uln",
    "first_name",
    "last_name",
    "email",
    "employer",
    "apprenticeship_programme",
    "level",
    "start_date",
    "planned_end_date",
    "cohort_name",
]

LEARNER_IMPORT_REQUIRED_COLUMNS = [
    "learner_reference",
    "first_name",
    "last_name",
    "apprenticeship_programme",
    "level",
    "start_date",
]

# Maps the CSV's user-facing snake_case headers to the internal camelCase
# LearnerInput/LearnerUpdate field names -- a template contract, not an API
# contract, so this mapping is applied once at parse time and nowhere else.
LEARNER_IMPORT_COLUMN_TO_FIELD = {
    "learner_reference": "learnerRef",
    "uln": "uln",
    "first_name": "firstName",
    "last_name": "lastName",
    "email": "email",
    "employer": "employer",
    "apprenticeship_programme": "programme",
    "level": "level",
    "start_date": "startDate",
    "planned_end_date": "plannedEndDate",
    "cohort_name": "cohortName",
}

TUTOR_IMPORT_COLUMNS = ["first_name", "last_name", "email", "employee_ref", "phone", "active", "external_system_id"]
TUTOR_IMPORT_REQUIRED_COLUMNS = ["first_name", "last_name", "email"]
TUTOR_IMPORT_COLUMN_TO_FIELD = {
    "first_name": "firstName",
    "last_name": "lastName",
    "email": "email",
    "employee_ref": "employeeRef",
    "phone": "phone",
    "active": "active",
    "external_system_id": "externalSystemId",
}

MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024  # 5MB
MAX_IMPORT_ROWS = 5000
MAX_IMPORT_FIELD_LENGTH = 500


class CsvParseError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def parse_import_csv(
    raw: bytes,
    *,
    column_to_field: dict[str, str],
    required_columns: list[str],
    max_bytes: int = MAX_IMPORT_FILE_BYTES,
    max_rows: int = MAX_IMPORT_ROWS,
    max_field_length: int = MAX_IMPORT_FIELD_LENGTH,
) -> list[dict[str, str]]:
    """Parses raw uploaded bytes into a list of {snake_case_column: value}
    dicts, one per data row. Raises CsvParseError with a user-facing
    message on any structural problem so the caller can always return a
    clean 400, never a 500. Deliberately strict: malformed quoting, ragged
    rows, and unrecognised/duplicate/missing headers are all rejected up
    front rather than silently producing wrong data.

    Generic over the column set so it can back both the learner and tutor
    controlled importers -- the parsing mechanics (BOM-strip, decode,
    header validation, row/field caps) have no entity-specific logic; only
    the column names differ."""
    if len(raw) > max_bytes:
        raise CsvParseError(f"File exceeds the maximum size of {max_bytes // (1024 * 1024)}MB")
    if not raw.strip():
        raise CsvParseError("File is empty")

    body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise CsvParseError("File must be UTF-8 encoded") from None

    try:
        raw_rows = list(csv.reader(io.StringIO(text), strict=True))
    except csv.Error as exc:
        raise CsvParseError(f"Malformed CSV: {exc}") from None

    raw_rows = [r for r in raw_rows if any((cell or "").strip() for cell in r)]
    if not raw_rows:
        raise CsvParseError("File has no data rows")

    header = [(h or "").strip() for h in raw_rows[0]]
    if len(header) != len(set(header)):
        raise CsvParseError("Duplicate column header in file")
    unknown = [h for h in header if h not in column_to_field]
    if unknown:
        raise CsvParseError(f"Unrecognised column(s): {', '.join(unknown)}")
    missing = [c for c in required_columns if c not in header]
    if missing:
        raise CsvParseError(f"Missing required column(s): {', '.join(missing)}")

    data_rows = raw_rows[1:]
    if len(data_rows) > max_rows:
        raise CsvParseError(f"File exceeds the maximum of {max_rows} rows")

    parsed = []
    for line_number, values in enumerate(data_rows, start=2):
        if len(values) != len(header):
            raise CsvParseError(f"Row {line_number} has {len(values)} column(s), expected {len(header)}")
        row = {}
        for col, val in zip(header, values):
            val = (val or "").strip()
            if len(val) > max_field_length:
                raise CsvParseError(f"Row {line_number}: value for '{col}' exceeds {max_field_length} characters")
            row[col] = val
        parsed.append(row)
    return parsed


def parse_learner_import_csv(
    raw: bytes, *, max_bytes: int = MAX_IMPORT_FILE_BYTES, max_rows: int = MAX_IMPORT_ROWS
) -> list[dict[str, str]]:
    return parse_import_csv(
        raw,
        column_to_field=LEARNER_IMPORT_COLUMN_TO_FIELD,
        required_columns=LEARNER_IMPORT_REQUIRED_COLUMNS,
        max_bytes=max_bytes,
        max_rows=max_rows,
    )


def parse_tutor_import_csv(
    raw: bytes, *, max_bytes: int = MAX_IMPORT_FILE_BYTES, max_rows: int = MAX_IMPORT_ROWS
) -> list[dict[str, str]]:
    return parse_import_csv(
        raw,
        column_to_field=TUTOR_IMPORT_COLUMN_TO_FIELD,
        required_columns=TUTOR_IMPORT_REQUIRED_COLUMNS,
        max_bytes=max_bytes,
        max_rows=max_rows,
    )
