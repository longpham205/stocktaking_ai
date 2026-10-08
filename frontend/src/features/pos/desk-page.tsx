import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { Plus } from 'lucide-react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm-dialog';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useMe } from '@/features/auth/use-auth';
import { addItem, createOrder, submitCapture } from '@/features/pos/api';
import { DeskCamera, type DeskView } from '@/features/pos/desk-camera';
import { newIdempotencyKey, preparePhoto, timeOfDay } from '@/features/pos/lib';
import { LineItem } from '@/features/pos/line-item';
import { ProductPicker } from '@/features/pos/product-picker';
import type { Order, OrderItem } from '@/features/pos/types';
import { useBarcodeScanner } from '@/features/pos/use-barcode-scanner';
import { useCamera } from '@/features/pos/use-camera';
import { useShortcuts } from '@/features/pos/use-desk';
import { useJob } from '@/features/pos/use-job';
import { addByBarcode, errorText, orderQuery, useOrderActions } from '@/features/pos/use-order';
import { formatVnd } from '@/lib/format';
import { qk } from '@/lib/query-keys';

/** The camera picked on this computer (a convenience: without storage the default camera is used). */
const CAMERA_KEY = 'pos.cameraId';

function storedCamera(): string | undefined {
  try {
    return window.localStorage.getItem(CAMERA_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

function storeCamera(deviceId: string): void {
  try {
    window.localStorage.setItem(CAMERA_KEY, deviceId);
  } catch {
    // not remembered: asked again next time
  }
}

function Kbd({ children }: { children: string }) {
  return <kbd className="ml-1 rounded border border-current/30 px-1 text-[11px] font-normal opacity-70">{children}</kbd>;
}

interface DeskPageProps {
  /** none: no order yet (`/pos`); the first photo or product starts one */
  orderId?: number;
  jobId?: number;
  resumed?: boolean;
}

/**
 * The counter screen of a desktop (webcam or a phone connected as one): the camera on the left,
 * the basket on the right, both always on screen. Photo after photo adds to the order without
 * leaving the page; Space takes a photo, F2 pays, F4 adds a product by name.
 */
export function DeskPage({ orderId, jobId, resumed }: DeskPageProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const settings = useMe().data?.settings;
  const [cameraId, setCameraId] = useState(storedCamera);
  const camera = useCamera(cameraId);
  const { job, waiting, slow } = useJob(jobId, orderId ?? 0);
  const order = useQuery({ ...orderQuery(orderId ?? 0), enabled: orderId !== undefined }).data;
  const actions = useOrderActions(orderId ?? 0);
  const { confirm, dialog } = useConfirm();
  const [view, setView] = useState<DeskView>('camera');
  const [focusItem, setFocusItem] = useState<number | null>(null);
  const [picker, setPicker] = useState<{ item: OrderItem | null } | null>(null);
  const [dismissed, setDismissed] = useState({ overlap: false, unrecognised: false });

  const submit = useMutation({
    mutationFn: async (photo: Blob) => {
      const prepared = await preparePhoto(photo);
      const target = orderId ?? (await createOrder()).id;
      const { job_id } = await submitCapture(target, prepared, newIdempotencyKey());
      return { target, jobId: job_id };
    },
    onSuccess: ({ target, jobId: next }) => {
      void navigate({ to: '/pos/orders/$orderId', params: { orderId: String(target) }, search: { job: next } });
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const busy = submit.isPending || waiting;

  useEffect(() => {
    if (resumed && orderId !== undefined) toast(`Đã khôi phục đơn #${orderId}`);
  }, [resumed, orderId]);

  useEffect(() => {
    // a new photo: its warnings show even if the last ones were dismissed
    setDismissed({ overlap: false, unrecognised: false });
  }, [jobId]);

  useEffect(() => {
    // recognised: show the photo with its boxes, to check them against the lines
    if (job?.status === 'done' && job.order?.captures.length) setView('photo');
  }, [job?.status, job?.order]);

  useEffect(() => {
    // paid or voided elsewhere (another tab, an admin): nothing to edit here any more
    if (order?.status === 'paid') void navigate({ to: '/pos/orders/$orderId/done', params: { orderId: String(order.id) } });
    if (order?.status === 'void') void navigate({ to: '/pos' });
  }, [order?.status, order?.id, navigate]);

  /** The order now holds `next`: shown at once, and the address follows a new order. */
  function show(next: Order) {
    queryClient.setQueryData(qk.order(next.id), next);
    if (next.id !== orderId) void navigate({ to: '/pos/orders/$orderId', params: { orderId: String(next.id) } });
  }

  useBarcodeScanner((code) => {
    void (async () => {
      try {
        const added = await addByBarcode(code, order);
        if (!added) toast.error('Không có sản phẩm khớp mã này — thử tìm theo tên');
        else {
          show(added.order);
          toast.success(`Đã thêm: ${added.name}`);
        }
      } catch (error) {
        toast.error(errorText(error));
      }
    })();
  }, !picker && !busy);

  async function shoot() {
    if (busy) return;
    const photo = await camera.grab();
    if (!photo) {
      toast.error('Camera chưa sẵn sàng');
      return;
    }
    setView('camera');
    submit.mutate(photo);
  }

  function sendPhoto(photo: Blob) {
    if (busy) return;
    setView('camera');
    submit.mutate(photo);
  }

  const blocked = !!order && order.missing_price_count > 0 && !settings?.allow_checkout_without_price;

  function toPay() {
    if (!order || order.items.length === 0) return;
    if (blocked) {
      document.querySelector<HTMLInputElement>('[data-price-input]')?.focus();
      toast.error('Còn sản phẩm chưa có giá — hãy nhập giá tay trước khi thanh toán');
      return;
    }
    void navigate({ to: '/pos/orders/$orderId/pay', params: { orderId: String(order.id) } });
  }

  useShortcuts({ ' ': () => void shoot(), F2: toPay, F4: () => setPicker({ item: null }) }, !picker);

  function toggleFocus(itemId: number) {
    setFocusItem((current) => (current === itemId ? null : itemId));
  }

  /** A box was clicked: mark it, and bring its line into view. */
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

  async function pick(productId: string) {
    const fixing = picker?.item;
    const done = { onSuccess: () => setPicker(null) };
    if (fixing) actions.update.mutate({ itemId: fixing.id, change: { product_id: productId } }, done);
    else if (order) actions.add.mutate({ productId }, done);
    else {
      try {
        show(await addItem((await createOrder()).id, productId));
        setPicker(null);
      } catch (error) {
        toast.error(errorText(error));
      }
    }
  }

  async function cancelOrder() {
    if (!order) return;
    if (order.items.length && !(await confirm('Huỷ đơn hiện tại và bắt đầu đơn mới?', 'Huỷ đơn', true))) return;
    actions.cancel.mutate(undefined, { onSuccess: () => void navigate({ to: '/pos' }) });
  }

  const overlap = !dismissed.overlap && job?.warnings?.some((w) => w.type === 'overlap_detected');
  const unrecognisedWarning = job?.warnings?.find((w) => w.type === 'unrecognized_objects');
  const unrecognised = !dismissed.unrecognised && unrecognisedWarning?.type === 'unrecognized_objects' ? unrecognisedWarning.count : 0;
  const items = order?.items ?? [];

  return (
    <div className="grid h-[calc(100dvh-7rem)] min-h-[34rem] grid-cols-[minmax(0,1fr)_26rem] gap-4">
      <DeskCamera
        camera={camera}
        cameraId={cameraId}
        onCamera={(deviceId) => {
          storeCamera(deviceId);
          setCameraId(deviceId);
        }}
        order={order}
        view={view}
        onView={setView}
        busy={busy}
        progress={waiting ? { position: job?.position ?? 0, reloading: !!job?.system_reloading, slow } : null}
        focusItem={focusItem}
        onFocus={focusLine}
        onAddRejected={() => setPicker({ item: null })}
        onShoot={() => void shoot()}
        onPhoto={sendPhoto}
      />

      <aside className="flex min-h-0 flex-col rounded-xl border border-border bg-card" aria-label="Giỏ hàng">
        <div className="flex items-baseline justify-between border-b border-border px-4 py-3">
          <h1 className="text-lg font-semibold">{order ? `Đơn #${order.id}` : 'Đơn mới'}</h1>
          {order && <span className="text-sm text-muted-foreground">{timeOfDay(order.created_at)}</span>}
        </div>

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {unrecognised > 0 && (
            <Card className="space-y-2 border-amber-300 bg-amber-50 p-3 text-sm">
              <p>🔎 Phát hiện thêm {unrecognised} vật chưa nhận diện được (khung đỏ). Hãy chụp gần hơn hoặc thêm món thủ công.</p>
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
              <p>📷 Có sản phẩm chồng lên nhau — nên tách ra rồi chụp thêm.</p>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => setView('camera')}>
                  Về camera
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setDismissed((d) => ({ ...d, overlap: true }))}>
                  Bỏ qua
                </Button>
              </div>
            </Card>
          )}
          {order && order.flagged_count > 0 && (
            <Card className="border-amber-300 bg-amber-50 p-3 text-sm">
              ⚠ Có {order.flagged_count} dòng cần xác nhận — bấm vào dòng viền vàng.
            </Card>
          )}

          {items.length === 0 ? (
            <p className="px-2 py-10 text-center text-sm text-muted-foreground">
              Chưa có sản phẩm. Đặt hàng dưới camera rồi bấm <b>Chụp</b> (Space), quét mã vạch, hoặc thêm món theo tên.
            </p>
          ) : (
            items.map((item) => (
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

        <div className="space-y-3 border-t border-border p-4">
          <Button variant="outline" className="w-full" onClick={() => setPicker({ item: null })}>
            <Plus className="h-4 w-4" />
            Thêm món theo tên
            <Kbd>F4</Kbd>
          </Button>
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">{order?.item_count ?? 0} sản phẩm</span>
            <span className="text-2xl font-bold" data-testid="order-total">
              {formatVnd(order?.total ?? 0)}
            </span>
          </div>
          <div className="flex gap-2">
            <Button variant="destructive" disabled={!order} onClick={() => void cancelOrder()}>
              Huỷ đơn
            </Button>
            <Button className="h-11 flex-1 text-base" disabled={items.length === 0} onClick={toPay}>
              {blocked ? `Nhập giá cho ${order?.missing_price_count} món` : 'Thanh toán'}
              <Kbd>F2</Kbd>
            </Button>
          </div>
        </div>
      </aside>

      <ProductPicker
        open={picker !== null}
        item={picker?.item ?? null}
        onClose={() => setPicker(null)}
        onPick={(productId) => void pick(productId)}
        onConfirm={(item) =>
          actions.update.mutate({ itemId: item.id, change: { confirm: true } }, { onSuccess: () => setPicker(null) })
        }
      />
      {dialog}
    </div>
  );
}
