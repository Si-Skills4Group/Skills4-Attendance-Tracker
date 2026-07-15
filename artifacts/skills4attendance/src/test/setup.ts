import '@testing-library/jest-dom/vitest';

// jsdom doesn't implement ResizeObserver; Radix UI components (e.g. Switch) use it.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as any).ResizeObserver ??= ResizeObserverStub;
