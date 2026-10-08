import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { Camera, Plus } from 'lucide-react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm-dialog';
import { RoutePending } from '@/components/route-states';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { meQuery } from '@/features/auth/use-auth';
import { RecognitionPending } from '@/features/pos/capture-page';
import { CaptureOverlay } from '@/features/pos/capture-overlay';
import { timeOfDay } from '@/features/pos/lib';
import { LineItem } from '@/features/pos/line-item';
import { ProductPicker } from '@/features/pos/product-picker';
import type { OrderItem } from '@/features/pos/types';
import { useBarcodeScanner } from '@/features/pos/use-barcode-scanner';
import { useJob } from '@/features/pos/use-job';
import { addByBarcode, errorText, orderQuery, useOrderActions } from '@/features/pos/use-order';
import { formatVnd } from '@/lib/format';
import { qk } from '@/lib/query-keys';

interface InvoicePageProps {
  orderId: number;
  jobId?: number;
  resumed?: boolean;
}

/** The order being sold: lines to check, the photos with their boxes, then payment. */
export function InvoicePage({ orderId, jobId, resumed }: InvoicePageProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const settings = useQuery(meQuery).data?.settings;
  const { job, waiting, slow } = useJob(jobId, orderId);
  const order = useQuery({ ...orderQuery(orderId), enabled: !waiting }).data;
  const actions = useOrderActions(orderId);
  const { confirm, dialog } = useConfirm();
  const [focusItem, setFocusItem] = useState<number | null>(null);
  const [picker, setPicker] = useState<{ item: OrderItem | null } | null>(null);
  const [dismissed, setDismissed] = useState({ overlap: false, unrecognised: false });

  useEffect(() => {
    if (resumed) toast(`Đã khôi phục đơn #${orderId}`);
  }, [resumed, orderId]);

  useEffect(() => {
    // paid or voided elsewhere (another tab, an admin): nothing to edit here any more
    if (order?.status === 'paid') void navigate({ to: '/pos/orders/$orderId/done', params: { orderId: String(orderId) } });
    if (order?.status === 'void') void navigate({ to: '/pos' });
  }, [order?.status, orderId, navigate]);

  useBarcodeScanner((code) => {
    void (async () => {
      try {
        const added = await addByBarcode(code, order);
        if (!added) toast.error('Không có sản phẩm khớp mã này — thử tìm theo tên');
        else {
          queryClient.setQueryData(qk.order(added.order.id), added.order);
          toast.success(`Đã thêm: ${added.name}`);
        }
      } catch (error) {
        toast.error(errorText(error));
      }
    })();
  }, !picker);

  if (waiting) return <RecognitionPending position={job?.position} reloading={job?.system_reloading} slow={slow} />;
  if (!order) return <RoutePending />;

  const overlap = !dismissed.overlap && job?.warnings?.some((w) => w.type === 'overlap_detected');
  const unrecognisedWarning = job?.warnings?.find((w) => w.type === 'unrecognized_objects');
  const unrecognised = !dismissed.unrecognised && unrecognisedWarning?.type === 'unrecognized_objects' ? unrecognisedWarning.count : 0;
  const blocked = order.missing_price_count > 0 && !settings?.allow_checkout_without_price;

  /** Mark a line and its boxes on the photo; the same one again clears the mark. */
  function toggleFocus(itemId: number) {
    setFocusItem((current) => (current === itemId ? null : itemId));
  }

  /** A box was tapped: mark it, and bring its line into view. */
  function focusLine(itemId: number) {
    toggleFocus(itemId);
    document.querySelector(`[data-line-id="${itemId}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  async function changeQuantity(item: OrderItem, quantity: number) {
    if (quantity >= 1) {
      actions.update.mutate({ itemId: item.id, change: { quantity } });
      return;
    }
    if (await confirm(`Xoá "${item.product_name}" khỏi đơn?`, 'Xoá', true)) actions.remove.mutate(item.id);
  }

  function pick(productId: string) {
    const fixing = picker?.item;
    const done = { onSuccess: () => setPicker(null) };
    if (fixing) actions.update.mutate({ itemId: fixing.id, change: { product_id: productId } }, done);
    else actions.add.mutate({ productId }, done);
  }

  async function cancelOrder() {
    if (order!.items.length && !(await confirm('Huỷ đơn hiện tại và bắt đầu đơn mới?', 'Huỷ đơn', true))) return;
    actions.cancel.mutate(undefined, { onSuccess: () => void navigate({ to: '/pos' }) });
  }

  function toPay() {
    if (blocked) {
      const first = document.querySelector<HTMLInputElement>('[data-price-input]');
      first?.scrollIntoView({ block: 'center' });
      first?.focus();
      toast.error('Còn sản phẩm chưa có giá — hãy nhập giá tay trước khi thanh toán');
      return;
    }
    void navigate({ to: '/pos/orders/$orderId/pay', params: { orderId: String(orderId) } });
  }

  const toCamera = () => void navigate({ to: '/pos/orders/$orderId/capture', params: { orderId: String(orderId) } });

  return (
    <div className="mx-auto max-w-xl space-y-3 pb-36">
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold">Đơn #{order.id}</h1>
        <span className="text-sm text-muted-foreground">{timeOfDay(order.created_at)}</span>
      </div>

      {unrecognised > 0 && (
        <Card className="space-y-2 border-amber-300 bg-amber-50 p-3 text-sm">
          <p>
            🔎 Phát hiện thêm {unrecognised} vật chưa nhận diện được (có thể là sản phẩm chưa có trong hệ thống, hoặc ảnh chưa
            rõ). Hãy chụp gần hơn hoặc thêm món thủ công.
          </p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => setPicker({ item: null })}>
              Thêm món
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDismissed((d) => ({ ...d, unrecognised: true }))}>
              Bỏ qua
            </Button>
          </div>
        </Card>
      )}
      {overlap && (
        <Card className="space-y-2 border-amber-300 bg-amber-50 p-3 text-sm">
          <p>📷 Có sản phẩm chồng lên nhau — nên chụp thêm để nhận diện chính xác hơn.</p>
          <div className="flex gap-2">
            <Button size="sm" onClick={toCamera}>
              Chụp thêm
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDismissed((d) => ({ ...d, overlap: true }))}>
              Bỏ qua
            </Button>
          </div>
        </Card>
      )}
      {order.flagged_count > 0 && (
        <Card className="border-amber-300 bg-amber-50 p-3 text-sm">
          ⚠ Có {order.flagged_count} dòng cần xác nhận — chạm vào dòng viền vàng.
        </Card>
      )}

      <CaptureOverlay order={order} focusItem={focusItem} onFocus={focusLine} onAddRejected={() => setPicker({ item: null })} />

      <div className="space-y-2">
        {order.items.length === 0 ? (
          <Card className="p-6 text-center text-sm text-muted-foreground">
            Chưa có sản phẩm. Hãy chụp thêm hoặc thêm thủ công.
          </Card>
        ) : (
          order.items.map((item) => (
            <LineItem
              key={`${item.id}-${item.price_missing}`}
              item={item}
              focused={focusItem === item.id}
              onOpen={() => {
                if (item.flagged) {
                  setFocusItem(item.id);
                  setPicker({ item });
                } else toggleFocus(item.id);
              }}
              onQuantity={(quantity) => void changeQuantity(item, quantity)}
              onPrice={(price) => actions.update.mutate({ itemId: item.id, change: { manual_price: price } })}
            />
          ))
        )}
      </div>

      <div className="flex gap-2">
        <Button variant="outline" className="flex-1" onClick={toCamera}>
          <Camera className="h-4 w-4" />
          Chụp thêm
        </Button>
        <Button variant="outline" className="flex-1" onClick={() => setPicker({ item: null })}>
          <Plus className="h-4 w-4" />
          Thêm món
        </Button>
      </div>

      <div className="fixed inset-x-0 bottom-0 z-10 border-t border-border bg-background/95 p-4 backdrop-blur">
        <div className="mx-auto max-w-xl space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">{order.item_count} sản phẩm</span>
            <span className="text-xl font-bold" data-testid="order-total">
              {formatVnd(order.total)}
            </span>
          </div>
          <div className="flex gap-2">
            <Button variant="destructive" onClick={() => void cancelOrder()}>
              Huỷ đơn
            </Button>
            <Button className="flex-1" disabled={order.items.length === 0} onClick={toPay}>
              {blocked ? `Nhập giá cho ${order.missing_price_count} món` : 'Thanh toán'}
            </Button>
          </div>
        </div>
      </div>

      <ProductPicker
        open={picker !== null}
        item={picker?.item ?? null}
        onClose={() => setPicker(null)}
        onPick={pick}
        onConfirm={(item) =>
          actions.update.mutate({ itemId: item.id, change: { confirm: true } }, { onSuccess: () => setPicker(null) })
        }
      />
      {dialog}
    </div>
  );
}
