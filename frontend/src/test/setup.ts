import { afterEach, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// jsdom ships no ResizeObserver/IntersectionObserver; a component that constructs one
// unconditionally would otherwise throw on render in tests.
beforeEach(() => {
  if (!window.ResizeObserver) {
    window.ResizeObserver = class {
      observe = () => undefined;
      unobserve = () => undefined;
      disconnect = () => undefined;
    } as unknown as typeof ResizeObserver;
  }
  if (!window.IntersectionObserver) {
    window.IntersectionObserver = class {
      observe = () => undefined;
      unobserve = () => undefined;
      disconnect = () => undefined;
    } as unknown as typeof IntersectionObserver;
  }
  // the router restores the scroll position on navigation; jsdom does not implement it
  window.scrollTo = () => undefined;
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => undefined;
  }
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  window.sessionStorage.clear();
});
