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

// jsdom doesn't implement the Pointer Capture API; Radix's Select calls
// hasPointerCapture/releasePointerCapture on pointerdown/pointerup.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {};
}
