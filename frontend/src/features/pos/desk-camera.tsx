import { useRef, useState, type DragEvent } from 'react';
import { Camera, ImageUp, Images } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import { CaptureOverlay } from '@/features/pos/capture-overlay';
import type { Order } from '@/features/pos/types';
import { cameraFailureText, type useCamera } from '@/features/pos/use-camera';
import { cn } from '@/lib/utils';

const DESK_TAIL = ' Vẫn bán được: kéo thả ảnh vào đây, bấm "Chọn ảnh" hoặc quét mã vạch.';

export type DeskView = 'camera' | 'photo';

/** Where the recognition is at, while one runs. */
export interface DeskProgress {
  position: number;
  reloading: boolean;
  slow: boolean;
}

interface DeskCameraProps {
  camera: ReturnType<typeof useCamera>;
  cameraId: string | undefined;
  onCamera: (deviceId: string) => void;
  order: Order | undefined;
  view: DeskView;
  onView: (view: DeskView) => void;
  /** a photo is being sent or recognised: no second one meanwhile */
  busy: boolean;
  progress: DeskProgress | null;
  focusItem: number | null;
  onFocus: (itemId: number) => void;
  onAddRejected: () => void;
  onShoot: () => void;
  onPhoto: (photo: Blob) => void;
}

/**
 * The left half of the counter: the live camera, or the last photos with their boxes (the camera
 * then shrinks to a corner and keeps running). A photo file dropped on it is recognised too.
 */
export function DeskCamera(props: DeskCameraProps) {
  const { camera, order, view, busy, progress } = props;
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const captures = order?.captures ?? [];
  const showPhoto = view === 'photo' && captures.length > 0 && !busy;
  const last = captures[captures.length - 1];

  function dropped(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const photo = event.dataTransfer.files[0];
    if (photo?.type.startsWith('image/') && !busy) props.onPhoto(photo);
  }

  return (
    <section className="flex min-h-0 flex-col gap-2" aria-label="Camera">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-md border border-border p-0.5" role="group" aria-label="Khung bên trái">
          <Button size="sm" variant={showPhoto ? 'ghost' : 'secondary'} onClick={() => props.onView('camera')}>
            <Camera className="h-4 w-4" />
            Camera
          </Button>
          <Button
            size="sm"
            variant={showPhoto ? 'secondary' : 'ghost'}
            disabled={captures.length === 0 || busy}
            onClick={() => props.onView('photo')}
          >
            <Images className="h-4 w-4" />
            Ảnh đã chụp{captures.length > 0 && ` (${captures.length})`}
          </Button>
        </div>
        {camera.devices.length > 1 && (
          <select
            className="h-8 max-w-64 rounded-md border border-border bg-background px-2 text-sm"
            aria-label="Chọn camera"
            value={props.cameraId ?? ''}
            onChange={(event) => props.onCamera(event.target.value)}
          >
            {!props.cameraId && <option value="">Camera mặc định</option>}
            {camera.devices.map((device) => (
              <option key={device.deviceId} value={device.deviceId}>
                {device.label}
              </option>
            ))}
          </select>
        )}
        <Button size="sm" variant="outline" className="ml-auto" disabled={busy} onClick={() => fileInput.current?.click()}>
          <ImageUp className="h-4 w-4" />
          Chọn ảnh
        </Button>
      </div>

      <div
        className={cn(
          'relative min-h-0 flex-1 overflow-hidden rounded-xl bg-black',
          dragging && 'ring-4 ring-primary',
          showPhoto && 'bg-muted',
        )}
        data-testid="desk-stage"
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={dropped}
      >
        {showPhoto && order && (
          <div className="absolute inset-0 overflow-auto p-3">
            {/* as wide as fits the height, so the whole photo is on screen */}
            <div className="mx-auto" style={{ maxWidth: `calc((100dvh - 16rem) * ${last.width / last.height})` }}>
              <CaptureOverlay order={order} focusItem={props.focusItem} onFocus={props.onFocus} onAddRejected={props.onAddRejected} />
            </div>
          </div>
        )}

        <div
          className={cn(
            'overflow-hidden bg-black',
            showPhoto
              ? 'absolute right-3 bottom-3 z-10 aspect-video w-56 cursor-pointer rounded-lg border-2 border-white shadow-lg'
              : 'absolute inset-0',
          )}
          onClick={showPhoto ? () => props.onView('camera') : undefined}
          title={showPhoto ? 'Về camera' : undefined}
        >
          {camera.failure ? (
            <div className="flex h-full items-center justify-center p-6">
              <p className="max-w-md text-center text-sm text-white/80">
                Không xem trước được camera. {cameraFailureText(camera.failure, DESK_TAIL)}
              </p>
            </div>
          ) : (
            <video ref={camera.videoRef} className="h-full w-full object-contain" playsInline muted autoPlay />
          )}
        </div>

        {!showPhoto && !busy && !camera.failure && (
          <div className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 rounded-full bg-black/60 px-3 py-1 text-xs text-white">
            Đặt hàng dưới camera rồi bấm Chụp — hoặc kéo thả ảnh vào đây
          </div>
        )}
        {busy && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 bg-black/55 text-white" role="status">
            <Spinner />
            <p className="text-sm">
              Đang nhận diện sản phẩm…
              {progress && progress.position > 0 && ` (đứng thứ ${progress.position + 1} trong hàng chờ)`}
            </p>
            {progress?.reloading ? (
              <p className="max-w-sm text-center text-xs text-white/80">
                ⏳ Hệ thống đang nạp lại thiết lập (khoảng 30–60 giây) — ảnh sẽ được xử lý ngay sau đó, không cần chụp lại
              </p>
            ) : (
              progress?.slow && <p className="text-xs text-white/80">Có thể lâu hơn bình thường</p>
            )}
          </div>
        )}
      </div>

      <Button className="h-12 text-base" disabled={busy || !camera.ready} onClick={props.onShoot}>
        <Camera className="h-5 w-5" />
        Chụp
        <kbd className="ml-2 rounded border border-white/40 px-1.5 text-xs font-normal">Space</kbd>
      </Button>

      <input
        ref={fileInput}
        type="file"
        accept="image/*"
        className="hidden"
        aria-label="Ảnh từ máy"
        onChange={(event) => {
          const photo = event.target.files?.[0];
          if (photo) props.onPhoto(photo);
          event.target.value = '';
        }}
      />
    </section>
  );
}
