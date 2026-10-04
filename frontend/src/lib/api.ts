import { apiFetch } from '@/lib/api-client';
import type { LoginOut, MeOut } from '@/lib/types';

/** One function per route the shared layer calls; feature modules keep theirs next to them. */

export function login(username: string, password: string): Promise<LoginOut> {
  return apiFetch<LoginOut>('/api/auth/login', { method: 'POST', body: { username, password } });
}

export function logout(): Promise<{ ok: boolean }> {
  return apiFetch('/api/auth/logout', { method: 'POST' });
}

export function getMe(): Promise<MeOut> {
  return apiFetch<MeOut>('/api/me');
}
