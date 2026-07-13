// Generated Zod schemas coerce OpenAPI `format: date` / `format: date-time`
// fields to JS `Date` objects (via `zod.coerce.date()`), but our Drizzle
// schema stores calendar dates as plain "YYYY-MM-DD" strings (`date(...,
// {mode:"string"})`). Convert at the boundary whenever writing a coerced
// Date into one of those columns.
export const toDateOnly = (value: Date | string): string =>
  typeof value === "string" ? value.slice(0, 10) : value.toISOString().slice(0, 10);
