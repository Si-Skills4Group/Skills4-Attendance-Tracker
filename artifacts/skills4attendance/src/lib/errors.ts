/**
 * Extracts a user-facing message from a mutation error. The backend's
 * convention is to return `{ error: string }` response bodies, which the
 * generated `ApiError` class exposes as `err.data.error`; `ApiError.message`
 * is itself already a well-formatted, server-aware string (see
 * `custom-fetch.ts`'s `buildErrorMessage`), so it's a safe second choice
 * before falling back to a generic message.
 */
export function getErrorMessage(error: unknown, fallback = "Something went wrong. Please try again."): string {
  const data = (error as { data?: { error?: string } } | null | undefined)?.data;
  const message = (error as { message?: string } | null | undefined)?.message;
  return data?.error || message || fallback;
}
