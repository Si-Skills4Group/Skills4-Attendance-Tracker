import csv
import io

from pydantic import BaseModel

LEARNER_CSV_COLUMNS = [
    "learnerRef",
    "uln",
    "firstName",
    "lastName",
    "email",
    "employer",
    "programme",
    "level",
    "startDate",
    "plannedEndDate",
]

TUTOR_CSV_COLUMNS = [
    "firstName",
    "lastName",
    "email",
    "employeeRef",
    "active",
    "externalSystemId",
]


class PreviewCsvInput(BaseModel):
    csv: str


class ImportCsvInput(BaseModel):
    rows: list[dict[str, str]]


def parse_csv_to_rows(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    rows = []
    for row in reader:
        cleaned = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None}
        if any(cleaned.values()):
            rows.append(cleaned)
    return rows


def stringify_rows_to_csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: ("" if row.get(c) is None else row.get(c)) for c in columns})
    return buf.getvalue()
