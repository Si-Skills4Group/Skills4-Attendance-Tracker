import { parse } from "csv-parse/sync";
import { stringify } from "csv-stringify/sync";

export const LEARNER_CSV_COLUMNS = [
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
] as const;

export const parseCsvToRows = (csv: string): Record<string, string>[] => {
  const records: Record<string, string>[] = parse(csv, {
    columns: true,
    skip_empty_lines: true,
    trim: true,
  });
  return records;
};

export const stringifyRowsToCsv = (
  rows: Record<string, unknown>[],
  columns: readonly string[],
): string => {
  return stringify(rows, { header: true, columns: [...columns] });
};
