/**
 * Every backend response (success or error) carries an X-Correlation-Id
 * header, and every error response body also echoes it as `correlationId`
 * (see pyapp/correlation.py) -- read from either, body first since it
 * survives however the fetch wrapper happens to expose headers.
 */
export function getErrorCorrelationId(error: unknown): string | undefined {
  const data = (error as { data?: { correlationId?: string } } | null | undefined)?.data;
  return data?.correlationId || undefined;
}

/**
 * Extracts a user-facing message from a mutation error. The backend's
 * convention is to return `{ error: string }` response bodies, which the
 * generated `ApiError` class exposes as `err.data.error`; `ApiError.message`
 * is itself already a well-formatted, server-aware string (see
 * `custom-fetch.ts`'s `buildErrorMessage`), so it's a safe second choice
 * before falling back to a generic message.
 *
 * For an unexpected (5xx) failure, appends the correlation ID so a user
 * reporting the problem can hand support something to grep the logs for --
 * expected 4xx errors (validation, conflicts, permissions) already have a
 * specific, actionable message and don't need one.
 */
export function getErrorMessage(error: unknown, fallback = "Something went wrong. Please try again."): string {
  const data = (error as { data?: { error?: string } } | null | undefined)?.data;
  const message = (error as { message?: string } | null | undefined)?.message;
  const status = (error as { status?: number } | null | undefined)?.status;
  const base = data?.error || message || fallback;

  if (status !== undefined && status >= 500) {
    const correlationId = getErrorCorrelationId(error);
    if (correlationId) {
      return `${base} (Reference: ${correlationId})`;
    }
  }
  return base;
}
