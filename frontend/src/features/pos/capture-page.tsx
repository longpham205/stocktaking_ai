import { useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { Camera, ImageUp, Receipt } from 'lucide-react';
import { toast } from 'sonner';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Spinner } from '@/components/ui/spinner';
import { useMe } from '@/features/auth/use-auth';
import { createOrder, submitCapture } from '@/features/pos/api';
import { newIdempotencyKey, preparePhoto } from '@/features/pos/lib';
import { useBarcodeScanner } from '@/features/pos/use-barcode-scanner';
import { cameraFailureText, useCamera, useTilt } from '@/features/pos/use-camera';
import { addByBarcode, errorText, orderQuery } from '@/features/pos/use-order';
import type { Order } from '@/features/pos/types';
import { qk } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

/**
 * Photograph the basket. `/pos` starts an order; `/pos/orders/$id/capture` adds a photo to that
 * order (its accepted objects add up on the lines already there).
 */
export function CapturePage({ orderId }: { orderId?: number }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const settings = useMe().data?.settings;
  const { videoRef, failure, ready, grab } = useCamera();
  const tilt = useTilt();
  const libraryInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const current = orderId === undefined ? undefined : queryClient.getQueryData<Order>(orderQuery(orderId).queryKey);

  const submit = useMutation({
    mutationFn: async (photo: Blob) => {
      const prepared = await preparePhoto(photo);
      const target = orderId ?? (await createOrder()).id;
      const { job_id } = await submitCapture(target, prepared, newIdempotencyKey());
      return { target, jobId: job_id };
    },
    onSuccess: ({ target, jobId }) => {
      void navigate({ to: '/pos/orders/$orderId', params: { orderId: String(target) }, search: { job: jobId } });
    },
    onError: (error) => toast.error(errorText(error)),
  });

  useBarcodeScanner((code) => {
    void (async () => {
      try {
        const added = await addByBarcode(code, current ?? null);
        if (!added) {
          toast.error('Không có sản phẩm khớp mã này — thử tìm theo tên');
          return;
        }
        queryClient.setQueryData(qk.order(added.order.id), added.order);
        toast.success(`Đã thêm: ${added.name}`);
        void navigate({ to: '/pos/orders/$orderId', params: { orderId: String(added.order.id) } });
      } catch (error) {
        toast.error(errorText(error));
      }
    })();
  }, !submit.isPending);

  async function shoot() {
    if (submit.isPending) return;
    if (settings?.tilt_block_capture && tilt.tooTilted) {
      toast.error(`Máy đang nghiêng (${Math.round(tilt.angle ?? 0)}°) — hãy giữ thẳng rồi chụp`);
      return;
    }
    const photo = await grab();
    if (!photo) {
      toast.error('Camera chưa sẵn sàng');
      return;
    }
    submit.mutate(photo);
  }

  function picked(files: FileList | null) {
    const photo = files?.[0];
    if (photo) submit.mutate(photo);
  }

  if (submit.isPending) return <RecognitionPending />;

  return (
    <div className="mx-auto max-w-xl space-y-3">
      <h1 className="text-lg font-semibold">{orderId ? `Chụp thêm cho đơn #${orderId}` : 'Chụp rổ hàng'}</h1>
      {failure ? (
        <Card className="space-y-3 p-4">
          <p className="text-sm font-medium">Không xem trước được camera trong trang.</p>
          <p className="text-sm text-muted-foreground">{cameraFailureText(failure)}</p>
          <Button className="w-full" onClick={() => cameraInput.current?.click()}>
            <Camera className="h-4 w-4" />
            Chụp bằng camera máy
          </Button>
        </Card>
      ) : (
        <div className="relative overflow-hidden rounded-xl bg-black">
          <video ref={videoRef} className="aspect-[3/4] w-full object-cover" playsInline muted autoPlay />
          <div
            className={cn(
              'pointer-events-none absolute inset-6 rounded-lg border-2 border-dashed',
              tilt.tooTilted ? 'border-red-400' : 'border-white/70',
            )}
          />
          <div
            className={cn(
              'absolute top-3 left-1/2 -translate-x-1/2 rounded-full px-3 py-1 text-xs text-white',
              tilt.tooTilted ? 'bg-red-600/90' : 'bg-black/60',
            )}
          >
            {tilt.angle === null
              ? 'Đặt hàng vào khung rồi bấm chụp'
              : tilt.tooTilted
                ? `Nghiêng (${Math.round(tilt.angle)}°) — hãy chỉnh thẳng`
                : `Góc chụp chuẩn (${Math.round(tilt.angle)}°)`}
          </div>
          <button
            type="button"
            aria-label="Chụp"
            disabled={!ready}
            className="absolute bottom-4 left-1/2 h-16 w-16 -translate-x-1/2 rounded-full border-4 border-white bg-white/30 disabled:opacity-40"
            onClick={() => void shoot()}
          />
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" className="flex-1" onClick={() => libraryInput.current?.click()}>
          <ImageUp className="h-4 w-4" />
          Chọn ảnh
        </Button>
        {tilt.needsPermission && (
          <Button variant="outline" onClick={() => void tilt.requestPermission()}>
            Bật cảm biến nghiêng
          </Button>
        )}
        {orderId !== undefined && (
          <Link
            to="/pos/orders/$orderId"
            params={{ orderId: String(orderId) }}
            className={cn(buttonVariants(), 'flex-1')}
          >
            <Receipt className="h-4 w-4" />
            Về hoá đơn{current ? ` (${current.item_count})` : ''}
          </Link>
        )}
      </div>
      {/* two inputs: with `capture` a phone opens its camera, without it the photo library */}
      <input
        ref={libraryInput}
        type="file"
        accept="image/*"
        className="hidden"
        aria-label="Ảnh từ thư viện"
        onChange={(event) => {
          picked(event.target.files);
          event.target.value = '';
        }}
      />
      <input
        ref={cameraInput}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        aria-label="Ảnh từ camera máy"
        onChange={(event) => {
          picked(event.target.files);
          event.target.value = '';
        }}
      />
    </div>
  );
}

export function RecognitionPending({ position = 0, reloading = false, slow = false }: { position?: number; reloading?: boolean; slow?: boolean }) {
  return (
    <div className="mx-auto max-w-xl space-y-3 py-6" role="status">
      <div className="h-20 animate-pulse rounded-lg bg-muted" />
      <div className="h-20 animate-pulse rounded-lg bg-muted" />
      <div className="h-20 animate-pulse rounded-lg bg-muted" />
      <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
        <Spinner />
        Đang nhận diện sản phẩm…{position > 0 && ` (đứng thứ ${position + 1} trong hàng chờ)`}
      </p>
      {reloading ? (
        <p className="text-center text-xs text-muted-foreground">
          ⏳ Hệ thống đang nạp lại thiết lập (khoảng 30–60 giây) — ảnh của bạn sẽ được xử lý ngay sau đó, không cần chụp lại
        </p>
      ) : (
        slow && <p className="text-center text-xs text-muted-foreground">Có thể lâu hơn bình thường</p>
      )}
    </div>
  );
}
