import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { RoutePending } from '@/components/route-states';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { pressKey } from '@/features/pos/lib';
import { useShortcuts, useWideScreen } from '@/features/pos/use-desk';
import { orderQuery, useOrderActions } from '@/features/pos/use-order';
import { formatVnd } from '@/lib/format';
import { cn } from '@/lib/utils';

const QUICK = [50_000, 100_000, 200_000, 500_000];
const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '000', '0', '⌫'];
/** A barcode scanner types faster than this, then Enter: that Enter does not pay. */
const SCANNER_GAP_MS = 50;

/** Cash (what the customer hands over, the change) or a bank transfer by QR. */
export function PayPage({ orderId }: { orderId: number }) {
  const navigate = useNavigate();
  const order = useQuery(orderQuery(orderId)).data;
  const { pay } = useOrderActions(orderId);
  const [method, setMethod] = useState<'cash' | 'qr'>('cash');
  const [given, setGiven] = useState(0);
  const wide = useWideScreen();
  const lastDigit = useRef(0);

  useEffect(() => {
    if (order && order.status !== 'open') void navigate({ to: '/pos/orders/$orderId/done', params: { orderId: String(orderId) } });
  }, [order, orderId, navigate]);

  const back = () => void navigate({ to: '/pos/orders/$orderId', params: { orderId: String(orderId) } });
  const type = (key: string) => () => {
    lastDigit.current = Date.now();
    if (method === 'cash') setGiven((g) => pressKey(g, key));
  };
  // a keyboard at the counter: the amount typed in, Enter to pay, Escape to go back
  useShortcuts({
    ...Object.fromEntries([...'0123456789'].map((digit) => [digit, type(digit)])),
    Backspace: type('⌫'),
    Enter: () => {
      if (Date.now() - lastDigit.current > SCANNER_GAP_MS) confirmPayment();
    },
    Escape: back,
  });

  if (!order) return <RoutePending />;
  const change = given - order.total;

  function confirmPayment() {
    if (!order || pay.isPending || (method === 'cash' && given < order.total)) return;
    pay.mutate(method === 'cash' ? { method: 'cash', cash_given: given } : { method: 'qr' }, {
      onSuccess: () =>
        void navigate({ to: '/pos/orders/$orderId/done', params: { orderId: String(orderId) }, search: { fresh: true } }),
    });
  }

  return (
    <div className="mx-auto max-w-md space-y-4">
      <div>
        <div className="text-sm text-muted-foreground">Tổng thanh toán</div>
        <div className="text-3xl font-bold" data-testid="pay-total">
          {formatVnd(order.total)}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2" role="group" aria-label="Phương thức">
        <Button variant={method === 'cash' ? 'default' : 'outline'} onClick={() => setMethod('cash')}>
          💵 Tiền mặt
        </Button>
        <Button variant={method === 'qr' ? 'default' : 'outline'} onClick={() => setMethod('qr')}>
          📱 Chuyển khoản
        </Button>
      </div>

      {method === 'cash' ? (
        <>
          <Card className="space-y-2 p-4">
            <div className="text-sm text-muted-foreground">Khách đưa</div>
            <div className="text-2xl font-bold" data-testid="cash-given">
              {formatVnd(given)}
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Tiền thừa</span>
              <span data-testid="cash-change" className={cn('text-lg font-bold', change >= 0 ? 'text-emerald-600' : 'text-amber-600')}>
                {change >= 0 ? formatVnd(change) : `Còn thiếu ${formatVnd(-change)}`}
              </span>
            </div>
          </Card>
          <div className="grid grid-cols-4 gap-2">
            {QUICK.map((value) => (
              <Button key={value} variant="secondary" onClick={() => setGiven(value)}>
                {value / 1000}k
              </Button>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-2">
            {KEYS.map((key) => (
              <Button key={key} variant="outline" className="h-12 text-lg" onClick={() => setGiven((g) => pressKey(g, key))}>
                {key}
              </Button>
            ))}
          </div>
          <Button className="h-11 w-full" disabled={change < 0 || pay.isPending} onClick={confirmPayment}>
            Xác nhận thanh toán
          </Button>
        </>
      ) : (
        <>
          <div className="mx-auto flex aspect-square w-48 items-center justify-center rounded-lg border-2 border-dashed border-border text-sm text-muted-foreground">
            QR mô phỏng
          </div>
          <p className="text-center text-sm text-muted-foreground">Đang chờ xác nhận chuyển khoản…</p>
          <Button className="h-11 w-full" disabled={pay.isPending} onClick={confirmPayment}>
            Đã nhận được tiền
          </Button>
        </>
      )}
      <Button variant="ghost" className="w-full" onClick={back}>
        Huỷ
      </Button>
      {wide && (
        <p className="text-center text-xs text-muted-foreground">
          Bàn phím: gõ số tiền khách đưa · Backspace để xoá · Enter để xác nhận · Esc để quay lại
        </p>
      )}
    </div>
  );
}
