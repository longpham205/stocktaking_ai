import { useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Dialog } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/spinner';
import { testEvidence, type TestedObject } from '@/features/admin-products/api';
import { preparePhoto } from '@/features/pos/lib';
import { errorText } from '@/features/pos/use-order';

function Tested({ object }: { object: TestedObject }) {
  return (
    <Card className="space-y-1 p-3 text-sm" data-testid="tested-object">
      <div>
        <b>{object.name}</b> <span className="text-muted-foreground">(SKU {object.product_id})</span> ·{' '}
        {object.status === 'accepted' ? '🟩 chắc chắn' : '🟨 cần xác nhận'}
      </div>
      <div>
        OCR đọc: <b>{object.ocr_text || '—'}</b>
        {object.ocr_keyword_hits.length > 0 && ` → khớp từ khoá của SKU ${object.ocr_keyword_hits.join(', ')}`}
      </div>
      <div>
        Màu đo:{' '}
        {object.color_hex ? (
          <>
            <span className="inline-block h-3 w-3 rounded border border-border align-middle" style={{ background: object.color_hex }} />{' '}
            {object.color_hex}
            {object.color_nearest &&
              ` · gần nhất ${object.color_nearest.code} (khoảng cách RGB ${object.color_nearest.rgb_distance})`}
          </>
        ) : (
          '—'
        )}
      </div>
      <div>
        Mã vạch:{' '}
        {object.barcodes.length
          ? `${object.barcodes.join(', ')}${object.barcode_skus.length ? ` → SKU ${object.barcode_skus.join(', ')}` : ' → không SKU nào có mã này'}`
          : '—'}
      </div>
      <div className="text-xs text-muted-foreground">
        Plugin đã chạy: {object.plugins.length ? object.plugins.join(', ') : 'không (vật đã chắc chắn, không cần bằng chứng)'}
      </div>
    </Card>
  );
}

/**
 * One photo through the pipeline, nothing saved: what each plugin read and which catalog entries
 * it matches. To check that evidence just entered has an effect.
 */
export function EvidenceTestDialog({ onClose }: { onClose: () => void }) {
  const test = useMutation({
    mutationFn: async (photo: Blob) => testEvidence(await preparePhoto(photo)),
    onError: (error) => toast.error(errorText(error)),
  });
  const result = test.data;

  return (
    <Dialog open onClose={onClose} title="🧪 Thử bằng chứng" className="sm:max-w-xl">
      <p className="text-sm text-muted-foreground">
        Chọn một ảnh có sản phẩm: hệ thống nhận diện thử (không lưu) và cho biết OCR đọc được gì, khớp từ khoá của SKU nào,
        màu đo được, mã vạch đọc được.
      </p>
      <input
        type="file"
        accept="image/*"
        aria-label="Ảnh để thử"
        className="mt-3 block w-full text-sm"
        onChange={(event) => {
          const photo = event.target.files?.[0];
          if (photo) test.mutate(photo);
          event.target.value = '';
        }}
      />
      {test.isPending && (
        <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> Đang chạy nhận diện thử…
        </p>
      )}
      {result && !test.isPending && (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-muted-foreground">
            {result.items.length} vật nhận ra / {result.detected_count ?? '?'} vật phát hiện · {Math.round(result.processing_time_ms)} ms.
            Kết quả không được lưu.
          </p>
          {result.items.length ? (
            result.items.map((object, index) => <Tested key={index} object={object} />)
          ) : (
            <p className="text-sm text-muted-foreground">Không nhận ra vật nào.</p>
          )}
        </div>
      )}
      <Button variant="outline" className="mt-4 w-full" onClick={onClose}>
        Đóng
      </Button>
    </Dialog>
  );
}
