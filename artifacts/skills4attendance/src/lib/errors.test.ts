import { describe, expect, it } from 'vitest';
import { getErrorCorrelationId, getErrorMessage } from './errors';

describe('getErrorMessage', () => {
  it('prefers the backend-provided data.error field', () => {
    const err = { data: { error: 'Email already in use' }, message: 'HTTP 400 Bad Request' };
    expect(getErrorMessage(err)).toBe('Email already in use');
  });

  it('falls back to the error message when there is no data.error', () => {
    const err = { data: null, message: 'HTTP 500 Internal Server Error' };
    expect(getErrorMessage(err)).toBe('HTTP 500 Internal Server Error');
  });

  it('falls back to the default fallback string when nothing usable is present', () => {
    expect(getErrorMessage({})).toBe('Something went wrong. Please try again.');
    expect(getErrorMessage(null)).toBe('Something went wrong. Please try again.');
    expect(getErrorMessage(undefined)).toBe('Something went wrong. Please try again.');
  });

  it('accepts a custom fallback', () => {
    expect(getErrorMessage({}, 'Could not save changes.')).toBe('Could not save changes.');
  });

  it('does not throw on a plain Error instance', () => {
    expect(getErrorMessage(new Error('boom'))).toBe('boom');
  });

  it('appends the correlation ID for an unexpected (5xx) error', () => {
    const err = { status: 500, data: { error: 'internal_error', correlationId: 'abc-123' } };
    expect(getErrorMessage(err)).toBe('internal_error (Reference: abc-123)');
  });

  it('does not append a correlation ID for an ordinary 4xx error', () => {
    const err = { status: 400, data: { error: 'Email already in use', correlationId: 'abc-123' } };
    expect(getErrorMessage(err)).toBe('Email already in use');
  });

  it('does not append anything if a 5xx error has no correlation ID', () => {
    const err = { status: 500, data: { error: 'internal_error' } };
    expect(getErrorMessage(err)).toBe('internal_error');
  });
});

describe('getErrorCorrelationId', () => {
  it('reads the correlation ID from the error body', () => {
    expect(getErrorCorrelationId({ data: { correlationId: 'xyz-789' } })).toBe('xyz-789');
  });

  it('returns undefined when there is none', () => {
    expect(getErrorCorrelationId({})).toBeUndefined();
    expect(getErrorCorrelationId(null)).toBeUndefined();
  });
});
