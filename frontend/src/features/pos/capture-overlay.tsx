import { useState } from 'react';
import { Minus, Plus, ZoomIn } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import type { CaptureBox, CaptureImage, Order, OrderItem } from '@/features/pos/types';
import { cn } from '@/lib/utils';

type BoxState = 'ok' | 'unc' | 'rej' | 'gone';

/** The box's colour follows the line as it is NOW: a confirmed line turns green, a deleted one grey. */
function boxState(box: CaptureBox, items: Map<number, OrderItem>): BoxState {
  if (box.status === 'rejected') return 'rej';
  const item = box.item_id === null ? undefined : items.get(box.item_id);
  if (!item) return 'gone';
  return item.flagged ? 'unc' : 'ok';
}

const STATE_CLASS: Record<BoxState, string> = {
  ok: 'border-emerald-500 [&>span]:bg-emerald-600',
  unc: 'border-amber-400 [&>span]:bg-amber-500',
  rej: 'border-red-500 [&>span]:bg-red-600',
  gone: 'border-dashed border-slate-400 [&>span]:bg-slate-500',
};

const short = (text: string) => (text.length > 16 ? `${text.slice(0, 15)}…` : text);
const pct = (value: number, of: number) => `${((100 * value) / of).toFixed(2)}%`;

interface PhotoProps {
  capture: CaptureImage;
  items: Map<number, OrderItem>;
  focusItem: number | null;
  onFocus: (itemId: number) => void;
  onAddRejected: () => void;
}

function Photo({ capture, items, focusItem, onFocus, onAddRejected }: PhotoProps) {
  return (
    <div className="relative">
      <img src={capture.image_url} alt="Ảnh vừa chụp" className="block w-full rounded-md" />
      {capture.boxes.map((box, index) => {
        const state = boxState(box, items);
        const item = box.item_id === null ? undefined : items.get(box.item_id);
        const [x1, y1, x2, y2] = box.bbox;
        const focused = focusItem !== null && state !== 'rej' && box.item_id === focusItem;
        const label = state === 'rej' ? 'chưa nhận diện' : item ? short(item.product_name) : 'đã xoá';
        return (
          <button
            key={index}
            type="button"
            data-testid="capture-box"
            data-state={state}
            aria-label={label}
            className={cn(
              'absolute rounded-sm border-2 transition-opacity',
              STATE_CLASS[state],
              focusItem !== null && !focused && 'opacity-35',
              focused && 'ring-2 ring-primary',
            )}
            style={{ left: pct(x1, capture.width), top: pct(y1, capture.height), width: pct(x2 - x1, capture.width), height: pct(y2 - y1, capture.height) }}
            onClick={(event) => {
              event.stopPropagation();
              if (state === 'rej') onAddRejected(); // not recognised: add it by hand
              else if (box.item_id !== null) onFocus(box.item_id);
            }}
          >
            <span
              className={cn(
                'absolute left-0 max-w-full truncate rounded-sm px-1 text-[10px] leading-4 text-white',
                // a box at the top edge keeps its label inside, where it is not cut off
                y1 < capture.height * 0.06 ? 'top-0' : '-top-4',
              )}
            >
              {label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

interface CaptureOverlayProps {
  order: Order;
  focusItem: number | null;
  onFocus: (itemId: number) => void;
  onAddRejected: () => void;
}

/** The basket photos of the order with a box per object, linked both ways with the order lines. */
export function CaptureOverlay({ order, focusItem, onFocus, onAddRejected }: CaptureOverlayProps) {
  const [selected, setSelected] = useState<number | null>(null);
  const [zoom, setZoom] = useState<number | null>(null);
  if (order.captures.length === 0) return null;
  const index = selected === null || selected >= order.captures.length ? order.captures.length - 1 : selected;
  const capture = order.captures[index];
  const items = new Map(order.items.map((item) => [item.id, item]));
  const photo = { capture, items, focusItem, onFocus, onAddRejected };

  return (
    <div className="space-y-2">
      {order.captures.length > 1 && (
        <div className="flex flex-wrap gap-1">
          {order.captures.map((c, k) => (
            <Button key={c.id} size="sm" variant={k === index ? 'default' : 'outline'} onClick={() => setSelected(k)}>
              Lượt {k + 1}
            </Button>
          ))}
        </div>
      )}
      <div className="relative">
        <Photo {...photo} />
        <Button
          size="icon"
          variant="secondary"
          className="absolute right-2 bottom-2 opacity-90"
          aria-label="Phóng to ảnh"
          onClick={() => setZoom(1)}
        >
          <ZoomIn className="h-4 w-4" />
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        🟩 chắc chắn · 🟨 cần xác nhận · 🟥 chưa nhận diện (chạm để thêm thủ công) · chạm khung hoặc dòng để đánh dấu
      </p>
      <Dialog open={zoom !== null} onClose={() => setZoom(null)} title="Ảnh vừa chụp" className="sm:max-w-3xl">
        <div className="max-h-[65vh] overflow-auto">
          <div style={{ width: `${(zoom ?? 1) * 100}%` }}>
            <Photo {...photo} />
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <Button size="icon" variant="outline" aria-label="Thu nhỏ" onClick={() => setZoom((z) => Math.max(1, (z ?? 1) - 1))}>
            <Minus className="h-4 w-4" />
          </Button>
          <span className="w-8 text-center text-sm text-muted-foreground">{zoom}×</span>
          <Button size="icon" variant="outline" aria-label="Phóng to" onClick={() => setZoom((z) => Math.min(4, (z ?? 1) + 1))}>
            <Plus className="h-4 w-4" />
          </Button>
          <Button className="ml-auto" variant="outline" onClick={() => setZoom(null)}>
            Đóng
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
