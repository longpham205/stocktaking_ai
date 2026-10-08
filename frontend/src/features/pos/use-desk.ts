import { useEffect, useRef, useSyncExternalStore } from 'react';

/** From this width the POS is the counter screen (camera and basket side by side); below, the phone flow. */
const WIDE = '(min-width: 1024px)';

function subscribe(onChange: () => void): () => void {
  if (typeof window.matchMedia !== 'function') return () => undefined;
  const query = window.matchMedia(WIDE);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

const isWide = () => typeof window.matchMedia === 'function' && window.matchMedia(WIDE).matches;

/** True on a desktop or a tablet held sideways; follows the window as it is resized. */
export function useWideScreen(): boolean {
  return useSyncExternalStore(subscribe, isWide, () => false);
}

/**
 * Keyboard shortcuts of the counter, by `KeyboardEvent.key` (`' '` is the space bar). Ignored
 * while typing in a field, while a dialog is open, and with Ctrl/Alt/Cmd held. A handled key does
 * not also press the focused button (Enter after a click on "100k" pays, it does not click again).
 */
export function useShortcuts(keys: Record<string, () => void>, enabled = true): void {
  const current = useRef(keys);
  current.current = keys;

  useEffect(() => {
    if (!enabled) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.repeat || event.ctrlKey || event.altKey || event.metaKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable)) return;
      if (document.querySelector('[role="dialog"]')) return;      const action = current.current[event.key];
      if (!action) return;
      event.preventDefault(); // the space bar would also press the focused button, or scroll
      action();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [enabled]);
}
