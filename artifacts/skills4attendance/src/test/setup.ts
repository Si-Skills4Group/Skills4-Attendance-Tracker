import '@testing-library/jest-dom/vitest';

// jsdom doesn't implement ResizeObserver; Radix UI components (e.g. Switch) use it.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as any).ResizeObserver ??= ResizeObserverStub;

// jsdom doesn't implement scrollIntoView; cmdk (the Command/Combobox search
// list) calls it when the highlighted item changes.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
