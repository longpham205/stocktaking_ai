import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm-dialog';
import { RoutePending } from '@/components/route-states';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { useMe } from '@/features/auth/use-auth';
import { getHistory, voidOrder } from '@/features/pos/api';
import { groupByDay, timeOfDay } from '@/features/pos/lib';
import type { HistoryRange } from '@/features/pos/types';
import { errorText, orderQuery } from '@/features/pos/use-order';
import { formatDateTime, formatVnd } from '@/lib/format';
import { qk } from '@/lib/query-keys';

const RANGES: [HistoryRange, string][] = [
  ['today', 'Hôm nay'],
  ['7d', '7 ngày'],
  ['30d', '30 ngày'],
];

function OrderDetail({ orderId, onClose }: { orderId: number; onClose: () => void }) {
  const queryClient = useQueryClient();
  const isAdmin = useMe().data?.user.role === 'admin';
  const order = useQuery(orderQuery(orderId)).data;
  const { confirm, dialog } = useConfirm();
  const cancel = useMutation({
    mutationFn: () => voidOrder(orderId),
    onSuccess: (voided) => {
      queryClient.setQueryData(qk.order(orderId), voided);
      void queryClient.invalidateQueries({ queryKey: ['history'] });
      onClose();
    },
    onError: (error) => toast.error(errorText(error)),
  });

  return (
    <Dialog open onClose={onClose} title={`Đơn #${orderId}`}>
      {!order ? (
        <RoutePending />
      ) : (
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground">
            {formatDateTime(order.created_at)} · {order.status === 'paid' ? 'Đã thu' : order.status === 'void' ? 'Đã huỷ' : 'Đang mở'}
          </div>
          {order.items.map((item) => (
            <div key={item.id} className="flex justify-between gap-2 text-sm">
              <div>
                {item.product_name}
                <div className="text-xs text-muted-foreground">
                  {item.quantity} × {formatVnd(item.unit_price ?? 0)}
                </div>
              </div>
              <b>{formatVnd(item.line_total ?? 0)}</b>
            </div>
          ))}
          <div className="flex justify-between border-t border-border pt-2 font-semibold">
            <span>Tổng</span>
            <span>{formatVnd(order.total)}</span>
          </div>
          {isAdmin && order.status !== 'void' && (
            <Button
              variant="destructive"
              className="w-full"
              disabled={cancel.isPending}
              onClick={async () => {
                if (await confirm(`Huỷ đơn #${orderId}? Không thể hoàn tác.`, 'Huỷ đơn', true)) cancel.mutate();
              }}
            >
              Huỷ đơn này
            </Button>
          )}
          <Button variant="outline" className="w-full" onClick={onClose}>
            Đóng
          </Button>
        </div>
      )}
      {dialog}
    </Dialog>
  );
}

/** The cashier's own sales, by day; a paid order can be voided by an admin only. */
export function HistoryPage() {
  const [range, setRange] = useState<HistoryRange>('today');
  const [opened, setOpened] = useState<number | null>(null);
  const history = useQuery({ queryKey: qk.history(range), queryFn: () => getHistory(range) });

  return (
    <div className="mx-auto max-w-xl space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Lịch sử bán hàng</h1>
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
      {history.isPending ? (
        <RoutePending />
      ) : history.data?.length ? (
        groupByDay(history.data).map((day) => (
          <section key={day.key} className="space-y-1">
            <div className="flex justify-between px-1 text-xs font-medium text-muted-foreground">
              <span>{day.label}</span>
              <span>
                {day.count} đơn · {formatVnd(day.total)}
              </span>
            </div>
            {day.items.map((item) => (
              <button
                key={item.id}
                type="button"
                className="flex w-full items-center justify-between rounded-lg border border-border bg-card p-3 text-left text-sm hover:bg-secondary"
                onClick={() => setOpened(item.id)}
              >
                <span>
                  <b>#{item.id}</b> · {timeOfDay(item.created_at)} · {item.item_count} món
                </span>
                <span className="flex items-center gap-2">
                  <Badge variant={item.status === 'paid' ? 'success' : 'muted'}>{item.status === 'paid' ? 'Đã thu' : 'Đã huỷ'}</Badge>
                  <b>{formatVnd(item.total)}</b>
                </span>
              </button>
            ))}
          </section>
        ))
      ) : (
        <p className="py-10 text-center text-sm text-muted-foreground">Chưa có đơn nào.</p>
      )}
      {opened !== null && <OrderDetail orderId={opened} onClose={() => setOpened(null)} />}
    </div>
  );
}
