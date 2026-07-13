---
name: Orval date field coercion
description: Orval-generated zod schemas coerce dates differently for request/response bodies vs query params — handle each differently or writes/reads break.
---

For OpenAPI fields typed `format: date` or `date-time`, Orval's zod codegen
behaves differently depending on where the field lives:

- **Request body fields and response schema fields** get
  `zod.coerce.date()` — after `.safeParse()`, the field is a real JS `Date`
  object, even if your Drizzle column is a string-mode date
  (`date(..., { mode: "string" })`, expects `"YYYY-MM-DD"`). Every DB write
  of such a field must convert it back to a date-only string before
  inserting/updating, or the driver throws or silently stores wrong data.
- **Query-param schemas** (e.g. list/export endpoints with `dateFrom`/
  `dateTo`) get plain `zod.date()` (no `.coerce`), which does not reliably
  validate raw query strings arriving as strings. `.safeParse()` on the
  whole query object then spuriously fails for date fields.

**Why:** found while wiring attendance/allocation/report routes — DB inserts
failed type checks because `Date` objects were reaching drizzle string-mode
date columns, and unrelated to that, query-string date filters wouldn't
parse through the generated query schema at all.

**How to apply:** for body/response date fields, convert with a small
`toDateOnly(value: Date | string): string` helper right before every
DB write. For query-param date fields, skip whole-object `.safeParse()` for
those specific fields — read `dateFrom`/`dateTo` directly off `req.query` as
strings instead. (Exception: if a query schema's date fields are Date-typed
and going through `.safeParse()` works for it specifically — e.g. it validates
successfully — convert the resulting `Date` back to a string the same way as
body fields.) Apply this pattern consistently to every new route touching
date query params.
