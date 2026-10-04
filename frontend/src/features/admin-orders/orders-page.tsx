import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { RoutePending } from '@/components/route-states';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { OrderDetail } from '@/features/pos/history-page';
import type { HistoryItem, HistoryRange } from '@/features/pos/types';
import { apiFetch } from '@/lib/api-client';
import { formatDateTime, formatVnd } from '@/lib/format';
import { qk } from '@/lib/query-keys';

const RANGES: [HistoryRange, string][] = [
  ['today', 'Hôm nay'],
  ['7d', '7 ngày'],
  ['30d', '30 ngày'],
  ['all', 'Tất cả'],
];

/** Every cashier's paid and voided orders. The detail dialog voids one. */
export function AdminOrdersPage() {
  const [range, setRange] = useState<HistoryRange>('today');
  const [opened, setOpened] = useState<number | null>(null);
  const orders = useQuery({
    queryKey: qk.adminOrders(range),
    queryFn: async () => (await apiFetch<{ items: HistoryItem[] }>(`/api/admin/orders?range=${range}`)).items,
  });

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Đơn hàng</h1>
        <select
          aria-label="Khoảng thời gian"
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          value={range}
          onChange={(event) => setRange(event.target.value as HistoryRange)}
        >
          {RANGES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {orders.isPending ? (
        <RoutePending />
      ) : orders.data?.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="py-2 font-medium">Đơn</th>
                <th className="py-2 font-medium">Giờ</th>
                <th className="py-2 font-medium">Thu ngân</th>
                <th className="py-2 text-right font-medium">Món</th>
                <th className="py-2 text-right font-medium">Tổng</th>
                <th className="py-2 pl-3 font-medium">Trạng thái</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {orders.data.map((order) => (
                <tr key={order.id} className="border-b border-border">
                  <td className="py-2 font-medium">#{order.id}</td>
                  <td className="py-2 whitespace-nowrap">{formatDateTime(order.created_at)}</td>
                  <td className="py-2">{order.cashier}</td>
                  <td className="py-2 text-right">{order.item_count}</td>
                  <td className="py-2 text-right">{formatVnd(order.total)}</td>
                  <td className="py-2 pl-3">
                    <Badge variant={order.status === 'paid' ? 'success' : 'muted'}>
                      {order.status === 'paid' ? 'Đã thu' : 'Đã huỷ'}
                    </Badge>
                  </td>
                  <td className="py-2 text-right">
                    <Button size="sm" variant="outline" onClick={() => setOpened(order.id)}>
                      Chi tiết
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="py-10 text-center text-sm text-muted-foreground">Chưa có đơn nào.</p>
      )}
      {opened !== null && <OrderDetail orderId={opened} onClose={() => setOpened(null)} />}
    </div>
  );
}
