/**
 * The API's response bodies the shared layer uses (mirrors the `schemas.py` of each backend module).
 * Feature modules add their own next to their code.
 */

export type Role = 'staff' | 'admin';

export interface User {
  id: number;
  username: string;
  full_name: string;
  role: Role;
  has_seen_onboarding: boolean;
}

export interface Shift {
  id: number;
  started_at: string;
  total_collected: number;
}

export interface PosSettings {
  allow_checkout_without_price: boolean;
  similarity_threshold: number | null;
  min_confidence_accept: number | null;
  tilt_block_capture: boolean;
  auto_print_receipt: boolean;
}

export interface LoginOut {
  token: string;
  user: User;
}

export interface MeOut {
  user: User;
  shift: Shift;
  settings: PosSettings;
}

export interface Health {
  status: string;
  recognizer: string;
}
