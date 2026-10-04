import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  addItem,
  checkout,
  createOrder,
  deleteItem,
  getOrder,
  productsByBarcode,
  updateItem,
  voidOrder,
  type ItemChange,
  type Payment,
} from '@/features/pos/api';
import type { Order } from '@/features/pos/types';
import { ApiError } from '@/lib/api-client';
import { qk } from '@/lib/query-keys';

export const orderQuery = (orderId: number) =>
  queryOptions({ queryKey: qk.order(orderId), queryFn: () => getOrder(orderId) });

export function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Không kết nối được máy chủ — kiểm tra mạng';
}

/**
 * The order changes of the POS. Every route answers with the order as it is afterwards: that
 * answer replaces the cached order, so the screen never shows a stale line.
 */
export function useOrderActions(orderId: number) {
  const queryClient = useQueryClient();
  const store = (order: Order) => {
    queryClient.setQueryData(qk.order(order.id), order);
    void queryClient.invalidateQueries({ queryKey: qk.openOrder() });
  };
  const onError = (error: unknown) => {
    toast.error(errorText(error));
  };
  return {
    add: useMutation({
      mutationFn: ({ productId, quantity }: { productId: string; quantity?: number }) =>
        addItem(orderId, productId, quantity),
      onSuccess: store,
      onError,
    }),
    update: useMutation({
      mutationFn: ({ itemId, change: body }: { itemId: number; change: ItemChange }) => updateItem(orderId, itemId, body),
      onSuccess: store,
      onError,
    }),
    remove: useMutation({ mutationFn: (itemId: number) => deleteItem(orderId, itemId), onSuccess: store, onError }),
    pay: useMutation({ mutationFn: (payment: Payment) => checkout(orderId, payment), onSuccess: store, onError }),
    cancel: useMutation({ mutationFn: () => voidOrder(orderId), onSuccess: store, onError }),
  };
}

/** The cashier's order to add to: the open one, or a new one (an empty open order is reused). */
export async function ensureOrder(current: Order | null | undefined): Promise<Order> {
  return current && current.status === 'open' ? current : createOrder();
}

/** A scanned (or typed) barcode: the product joins the order. Returns the order, or null if no product. */
export async function addByBarcode(code: string, current: Order | null | undefined): Promise<{ order: Order; name: string } | null> {
  const found = await productsByBarcode(code.trim());
  if (found.length === 0) return null;
  const order = await ensureOrder(current);
  return { order: await addItem(order.id, found[0].id), name: found[0].name };
}
