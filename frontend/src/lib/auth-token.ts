/**
 * The login token. In sessionStorage, as the legacy web kept it: it dies with the tab, so a shared
 * counter PC does not stay logged in after the cashier closes the browser. Listeners hear every
 * change (a 401 anywhere clears it and the app goes back to the login page).
 */
const KEY = 'stocktaking.token';
const listeners = new Set<() => void>();

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null; // storage blocked (private mode on some browsers): the token lives in memory only
  }
}

let memory: string | null = null;

export function getToken(): string | null {
  return storage()?.getItem(KEY) ?? memory;
}

export function setToken(token: string): void {
  memory = token;
  storage()?.setItem(KEY, token);
  listeners.forEach((listener) => listener());
}

export function clearToken(): void {
  memory = null;
  storage()?.removeItem(KEY);
  listeners.forEach((listener) => listener());
}

export function onTokenChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
