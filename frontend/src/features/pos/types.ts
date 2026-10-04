/** Bodies of the POS routes (mirrors backend orders, captures, catalog and orders-history schemas). */

export interface OrderItem {
  id: number;
  product_id: string;
  product_name: string;
  quantity: number;
  /** null: no price and none typed in */
  unit_price: number | null;
  price_missing: boolean;
  /** the unit price was typed in by the cashier */
  manual_price: boolean;
  line_total: number | null;
  /** the recognizer was not sure: the cashier must confirm the line */
  flagged: boolean;
  thumbnail_url: string | null;
  evidence: Record<string, unknown> | null;
}

export type BoxStatus = 'accepted' | 'uncertain' | 'rejected';

export interface CaptureBox {
  /** the order line the object became; null for an object found but not recognised */
  item_id: number | null;
  product_id: string | null;
  status: BoxStatus;
  /** x1, y1, x2, y2 in pixels of the photo */
  bbox: [number, number, number, number];
}

export interface CaptureImage {
  id: number;
  created_at: string;
  image_url: string;
  width: number;
  height: number;
  boxes: CaptureBox[];
}

export type OrderStatus = 'open' | 'paid' | 'void';

export interface Order {
  id: number;
  status: OrderStatus;
  created_at: string;
  paid_at: string | null;
  payment_method: 'cash' | 'qr' | null;
  cash_given: number | null;
  change_given: number | null;
  items: OrderItem[];
  captures: CaptureImage[];
  item_count: number;
  total: number;
  missing_price_count: number;
  flagged_count: number;
}

export type JobWarning = { type: 'overlap_detected' } | { type: 'unrecognized_objects'; count: number };

export interface Job {
  status: 'queued' | 'processing' | 'done' | 'error';
  position: number;
  system_reloading: boolean;
  added?: number;
  warnings?: JobWarning[];
  order?: Order;
  error?: { code: string; message: string };
}

export interface Product {
  id: string;
  name: string;
  barcode: string;
  price: number | null;
  needs_naming: boolean;
  missing_color_reference: boolean;
}

export interface HistoryItem {
  id: number;
  status: 'paid' | 'void';
  created_at: string;
  paid_at: string | null;
  total: number;
  item_count: number;
  payment_method: 'cash' | 'qr' | null;
  cashier: string;
}

export type HistoryRange = 'today' | '7d' | '30d' | 'all';
