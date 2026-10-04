import { useEffect, useRef } from 'react';

/** A USB/Bluetooth scanner types like a keyboard: characters < 50 ms apart, then Enter. */
const MAX_GAP_MS = 50;
const MIN_LENGTH = 6;

/**
 * Calls `onScan(code)` for what a scanner types while no field has the focus (a person typing in
 * a field is never mistaken for a scan, and typing is too slow to look like one).
 */
export function useBarcodeScanner(onScan: (code: string) => void, enabled = true): void {
  const callback = useRef(onScan);
  callback.current = onScan;

  useEffect(() => {
    if (!enabled) return undefined;
    let buffer = '';
    let last = 0;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      const now = Date.now();
      if (event.key === 'Enter') {
        if (buffer.length >= MIN_LENGTH) callback.current(buffer);
        buffer = '';
        return;
      }
      if (event.key.length === 1) {
        buffer = now - last < MAX_GAP_MS ? buffer + event.key : event.key;
        last = now;
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [enabled]);
}
