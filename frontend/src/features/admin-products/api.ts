import type { Product } from '@/features/pos/types';
import { apiFetch } from '@/lib/api-client';

/** Mirrors backend catalog/schemas.py and audit/schemas.py, validation/schemas.py. */

export type ProductFilter = '' | 'missing_price' | 'missing_barcode' | 'needs_naming' | 'out_of_stock';

export interface AdminProducts {
  total: number;
  page: number;
  size: number;
  items: Product[];
  /** over the whole catalog on sale, whatever the search and filter */
  missing_price: number;
  missing_barcode: number;
  needs_naming: number;
  out_of_stock: number;
}

export interface ColorRef {
  code: string;
  /** null: a product names this colour but it has no reference yet */
  hex: string | null;
  used_by: string[];
  missing: boolean;
}

export interface Evidence {
  ocr_keywords: string[];
  color_code: string | null;
  force_evidence: string[];
  confusable_with: string[];
}

export interface EvidenceView {
  product: Product;
  evidence: Evidence;
  colors: ColorRef[];
  /** what the admin ticks before saving */
  confirm_text: string;
  ocr_min_length: number;
  /** signed URLs of the product's gallery photos */
  gallery: string[];
  warnings: string[];
}

export interface ChangeEntry {
  id: number;
  table: string;
  record_id: string;
  field: string;
  old: string | null;
  new: string | null;
  by: string | null;
  at: string;
  name: string | null;
}

export interface TestedObject {
  product_id: string;
  name: string;
  status: 'accepted' | 'uncertain';
  ocr_text: string;
  ocr_keyword_hits: string[];
  color_hex: string | null;
  color_nearest: { code: string; rgb_distance: number } | null;
  barcodes: string[];
  barcode_skus: string[];
  plugins: string[];
}

export interface EvidenceTest {
  items: TestedObject[];
  detected_count: number | null;
  processing_time_ms: number;
}

export function getAdminProducts(search: string, filter: ProductFilter, page: number): Promise<AdminProducts> {
  const query = `page=${page}&filter=${encodeURIComponent(filter)}&search=${encodeURIComponent(search)}`;
  return apiFetch<AdminProducts>(`/api/admin/products?${query}`);
}

export interface ProductChange {
  price?: number | null;
  /** null: stop tracking the product's stock */
  stock?: number | null;
  barcode?: string;
  name?: string;
}

export function updateProduct(productId: string, change: ProductChange): Promise<Product> {
  return apiFetch<Product>(`/api/admin/products/${encodeURIComponent(productId)}`, { method: 'PATCH', body: change });
}

export function getEvidence(productId: string): Promise<EvidenceView> {
  return apiFetch<EvidenceView>(`/api/admin/products/${encodeURIComponent(productId)}/evidence`);
}

export function updateEvidence(productId: string, evidence: Evidence): Promise<EvidenceView> {
  return apiFetch<EvidenceView>(`/api/admin/products/${encodeURIComponent(productId)}/evidence`, {
    method: 'PATCH',
    body: { ...evidence, confirm: true },
  });
}

export function updateColor(code: string, hex: string | null): Promise<ColorRef> {
  return apiFetch<ColorRef>(`/api/admin/colors/${encodeURIComponent(code)}`, { method: 'PATCH', body: { hex, confirm: true } });
}

export async function getChangeLog(table: string, record?: string): Promise<ChangeEntry[]> {
  const query = `table=${table}${record === undefined ? '' : `&record=${encodeURIComponent(record)}`}`;
  return (await apiFetch<{ items: ChangeEntry[] }>(`/api/admin/change-log?${query}`)).items;
}

export function revertChange(entryId: number, advancedPassword?: string): Promise<unknown> {
  return apiFetch(`/api/admin/change-log/${entryId}/revert`, {
    method: 'POST',
    body: advancedPassword === undefined ? {} : { advanced_password: advancedPassword },
  });
}

export function testEvidence(photo: Blob): Promise<EvidenceTest> {
  return apiFetch<EvidenceTest>('/api/admin/evidence-test', {
    method: 'POST',
    body: photo,
    headers: { 'Content-Type': photo.type || 'image/jpeg' },
  });
}
