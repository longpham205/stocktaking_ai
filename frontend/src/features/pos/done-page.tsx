import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { CircleCheck, Printer } from 'lucide-react';
import { RoutePending } from '@/components/route-states';
import { Button } from '@/components/ui/button';
import { useMe } from '@/features/auth/use-auth';
import type { Order } from '@/features/pos/types';
import { orderQuery } from '@/features/pos/use-order';
import { formatVnd } from '@/lib/format';

/** The receipt as printed: only this block is on paper (see `@media print` in index.css). */
export function Receipt({ order }: { order: Order }) {
  return (
    <div id="print-area" className="hidden font-mono text-xs print:block">
      <b>Stocktaking POS</b>
      <div>
        Đơn #{order.id} · {new Date(order.paid_at ?? order.created_at).toLocaleString('vi-VN')}
      </div>
      <hr />
      {order.items.map((item) => (
        <div key={item.id}>
          {item.product_name}
          <div>
            &nbsp;&nbsp;{item.quantity} x {formatVnd(item.unit_price ?? 0)} = {formatVnd(item.line_total ?? 0)}
          </div>
        </div>
      ))}
      <hr />
      <b>TỔNG: {formatVnd(order.total)}</b>
      {order.payment_method === 'cash' ? (
        <div>
          Tiền mặt: {formatVnd(order.cash_given ?? 0)}
          <br />
          Tiền thừa: {formatVnd(order.change_given ?? 0)}
        </div>
      ) : (
        <div>Chuyển khoản</div>
      )}
    </div>
  );
}

/** After the payment: the amount, the change, print, next customer. */
export function DonePage({ orderId, fresh }: { orderId: number; fresh?: boolean }) {
  const navigate = useNavigate();
  const order = useQuery(orderQuery(orderId)).data;
  const autoPrint = useMe().data?.settings.auto_print_receipt;
  const printed = useRef(false);

  useEffect(() => {
    // printed once, right after the payment, when the shop asked for it (not on a later visit)
    if (fresh && autoPrint && order?.status === 'paid' && !printed.current) {
      printed.current = true;
      setTimeout(() => window.print(), 300);
    }
  }, [fresh, autoPrint, order?.status]);

  if (!order) return <RoutePending />;
  return (
    <div className="mx-auto max-w-md space-y-3 pt-6 text-center">
      <CircleCheck className="mx-auto h-14 w-14 text-emerald-500" aria-hidden="true" />
      <h1 className="text-xl font-semibold">{order.status === 'void' ? 'Đơn đã huỷ' : 'Thanh toán thành công'}</h1>
      <div className="text-3xl font-bold">{formatVnd(order.total)}</div>
      <p className="text-muted-foreground">{order.payment_method === 'cash' ? 'Tiền mặt' : 'Chuyển khoản'}</p>
      {order.payment_method === 'cash' && (
        <p>
          Khách đưa <b>{formatVnd(order.cash_given ?? 0)}</b> · Tiền thừa{' '}
          <b className="text-emerald-600" data-testid="done-change">
            {formatVnd(order.change_given ?? 0)}
          </b>
        </p>
      )}
      <div className="flex gap-2 pt-2">
        <Button variant="outline" className="flex-1" onClick={() => window.print()}>
          <Printer className="h-4 w-4" />
          In hoá đơn
        </Button>
        <Button className="flex-1" onClick={() => void navigate({ to: '/pos' })}>
          Đơn mới
        </Button>
      </div>
      <Receipt order={order} />
    </div>
  );
}
