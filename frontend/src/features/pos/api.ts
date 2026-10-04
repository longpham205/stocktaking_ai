import { apiFetch } from '@/lib/api-client';
import type { HistoryItem, HistoryRange, Job, Order, Product } from '@/features/pos/types';

/** Every POS route, one function each. Changes answer with the order as it is afterwards. */

export function createOrder(): Promise<Order> {
  return apiFetch<Order>('/api/orders', { method: 'POST' });
}

export async function getOpenOrder(): Promise<Order | null> {
  return (await apiFetch<{ order: Order | null }>('/api/orders/open')).order;
}

export function getOrder(orderId: number): Promise<Order> {
  return apiFetch<Order>(`/api/orders/${orderId}`);
}

export function addItem(orderId: number, productId: string, quantity = 1): Promise<Order> {
  return apiFetch<Order>(`/api/orders/${orderId}/items`, {
    method: 'POST',
    body: { product_id: productId, quantity },
  });
}

export interface ItemChange {
  quantity?: number;
  manual_price?: number | null;
  confirm?: boolean;
  product_id?: string;
}

export function updateItem(orderId: number, itemId: number, change: ItemChange): Promise<Order> {
  return apiFetch<Order>(`/api/orders/${orderId}/items/${itemId}`, { method: 'PATCH', body: change });
}

export function deleteItem(orderId: number, itemId: number): Promise<Order> {
  return apiFetch<Order>(`/api/orders/${orderId}/items/${itemId}`, { method: 'DELETE' });
}

export type Payment = { method: 'cash'; cash_given: number } | { method: 'qr' };

export function checkout(orderId: number, payment: Payment): Promise<Order> {
  return apiFetch<Order>(`/api/orders/${orderId}/checkout`, { method: 'POST', body: payment });
}

export function voidOrder(orderId: number): Promise<Order> {
  return apiFetch<Order>(`/api/orders/${orderId}/void`, { method: 'POST' });
}

export function submitCapture(orderId: number, photo: Blob, idempotencyKey: string): Promise<{ job_id: number }> {
  return apiFetch(`/api/orders/${orderId}/captures`, {
    method: 'POST',
    body: photo,
    headers: { 'Idempotency-Key': idempotencyKey, 'Content-Type': photo.type || 'image/jpeg' },
  });
}

export function getJob(jobId: number): Promise<Job> {
  return apiFetch<Job>(`/api/jobs/${jobId}`);
}

export async function searchProducts(search: string): Promise<Product[]> {
  return (await apiFetch<{ items: Product[] }>(`/api/catalog/products?search=${encodeURIComponent(search)}`)).items;
}

export async function productsByBarcode(barcode: string): Promise<Product[]> {
  return (await apiFetch<{ items: Product[] }>(`/api/catalog/products?barcode=${encodeURIComponent(barcode)}`)).items;
}

export async function getHistory(range: HistoryRange): Promise<HistoryItem[]> {
  return (await apiFetch<{ items: HistoryItem[] }>(`/api/history?range=${range}`)).items;
}

export function markOnboardingSeen(): Promise<{ ok: boolean }> {
  return apiFetch('/api/me/onboarding-seen', { method: 'POST' });
}
