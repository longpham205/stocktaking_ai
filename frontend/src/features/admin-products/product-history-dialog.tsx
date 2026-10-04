import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm-dialog';
import { RoutePending } from '@/components/route-states';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { getChangeLog, revertChange } from '@/features/admin-products/api';
import { FIELD_LABEL, showValue } from '@/features/admin-products/lib';
import { errorText } from '@/features/pos/use-order';
import { formatDateTime } from '@/lib/format';
import { qk } from '@/lib/query-keys';

/**
 * Every change of one product (price, barcode, name and its recognition evidence), newest first.
 * A change is reverted only while the field still holds what that change wrote.
 */
export function ProductHistoryDialog({ productId, onClose }: { productId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { confirm, dialog } = useConfirm();
  const entries = useQuery({
    queryKey: qk.adminChangeLog(`product-${productId}`),
    queryFn: async () => {
      const [product, evidence] = await Promise.all([
        getChangeLog('product', productId),
        getChangeLog('product_evidence', productId),
      ]);
      return [...product, ...evidence].sort((a, b) => b.id - a.id);
    },
  });
  const revert = useMutation({
    mutationFn: (entryId: number) => revertChange(entryId),
    onSuccess: () => {
      toast.success('Đã hoàn tác');
      void queryClient.invalidateQueries({ queryKey: ['admin'] });
    },
    onError: (error) => toast.error(errorText(error)),
  });

  return (
    <Dialog open onClose={onClose} title={`Lịch sử thay đổi · SKU ${productId}`} className="sm:max-w-xl">
      {entries.isPending ? (
        <RoutePending />
      ) : entries.data?.length ? (
        <div className="space-y-3">
          {entries.data.map((entry) => (
            <div key={entry.id} className="flex items-center gap-3 text-sm" data-testid="change-entry">
              <div className="min-w-0 flex-1">
                <b>{FIELD_LABEL[entry.field] ?? entry.field}</b>: {showValue(entry.field, entry.old)} →{' '}
                <b>{showValue(entry.field, entry.new)}</b>
                <div className="text-xs text-muted-foreground">
                  {entry.by ?? '?'} · {formatDateTime(entry.at)}
                </div>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={revert.isPending}
                onClick={async () => {
                  if (await confirm('Hoàn tác thay đổi này?', 'Hoàn tác')) revert.mutate(entry.id);
                }}
              >
                Hoàn tác
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Chưa có thay đổi nào.</p>
      )}
      <Button variant="outline" className="mt-4 w-full" onClick={onClose}>
        Đóng
      </Button>
      {dialog}
    </Dialog>
  );
}
