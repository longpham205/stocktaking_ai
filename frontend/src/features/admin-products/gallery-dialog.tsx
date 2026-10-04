import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { RoutePending } from '@/components/route-states';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { getEvidence } from '@/features/admin-products/api';
import { qk } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

/**
 * The reference photos the recognizer compares against (the product's gallery folder), to check at
 * a glance that a product is the one its name says. The API serves them scaled down, by signed URL.
 */
export function GalleryDialog({ productId, onClose }: { productId: string; onClose: () => void }) {
  const view = useQuery({ queryKey: qk.adminEvidence(productId), queryFn: () => getEvidence(productId) }).data;
  const [selected, setSelected] = useState(0);
  const title = view ? `Ảnh gallery · SKU ${productId} — ${view.product.name}` : `Ảnh gallery · SKU ${productId}`;

  return (
    <Dialog open onClose={onClose} title={title} className="sm:max-w-2xl">
      {!view ? (
        <RoutePending />
      ) : view.gallery.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Sản phẩm này chưa có ảnh gallery (hoặc thư mục gallery của cấu hình đang chạy không chứa thư mục của nó).
        </p>
      ) : (
        <div className="space-y-3">
          <img
            src={view.gallery[Math.min(selected, view.gallery.length - 1)]}
            alt={`Ảnh ${selected + 1} của SKU ${productId}`}
            className="mx-auto max-h-[55vh] rounded-lg object-contain"
          />
          <div className="flex gap-2 overflow-x-auto pb-1">
            {view.gallery.map((url, index) => (
              <button
                key={url}
                type="button"
                aria-label={`Xem ảnh ${index + 1}`}
                className={cn('flex-none rounded border-2', index === selected ? 'border-primary' : 'border-transparent')}
                onClick={() => setSelected(index)}
              >
                <img src={url} alt="" className="h-16 w-16 rounded object-cover" />
              </button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {view.gallery.length} ảnh đầu tiên trong gallery của sản phẩm (đã thu nhỏ).
          </p>
        </div>
      )}
      <Button variant="outline" className="mt-4 w-full" onClick={onClose}>
        Đóng
      </Button>
    </Dialog>
  );
}
