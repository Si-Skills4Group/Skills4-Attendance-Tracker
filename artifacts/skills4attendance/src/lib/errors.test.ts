import { describe, expect, it } from 'vitest';
import { getErrorMessage } from './errors';

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
});
