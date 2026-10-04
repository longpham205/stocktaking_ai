import { useEffect, useState, type MouseEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { RoutePending } from '@/components/route-states';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { getEvidence, updateColor, updateEvidence } from '@/features/admin-products/api';
import { parseList, sampleColor } from '@/features/admin-products/lib';
import { errorText } from '@/features/pos/use-order';
import { qk } from '@/lib/query-keys';

const PLUGINS: [string, string][] = [
  ['ocr', 'OCR'],
  ['color', 'Màu'],
  ['barcode', 'Barcode'],
];

interface Draft {
  ocr: string;
  color: string;
  /** a reference colour to create or change for `color`; empty: leave the references alone */
  hex: string;
  force: string[];
  confusable: string;
}

function Swatch({ hex }: { hex: string }) {
  return <span className="inline-block h-4 w-4 rounded border border-border align-middle" style={{ background: hex }} />;
}

/** Pick a colour by tapping a gallery photo of the product. */
function ColorPicker({ url, onUse, onBack }: { url: string; onUse: (hex: string) => void; onBack: () => void }) {
  const [hex, setHex] = useState<string | null>(null);
  function pick(event: MouseEvent<HTMLImageElement>) {
    try {
      setHex(sampleColor(event.currentTarget, event.clientX, event.clientY));
    } catch {
      toast.error('Không đọc được màu từ ảnh');
    }
  }
  return (
    <div className="space-y-3">
      <p className="text-sm font-medium">Chạm vào vùng màu đặc trưng của sản phẩm</p>
      <img src={url} alt="Ảnh gallery" className="w-full cursor-crosshair rounded-lg" onClick={pick} />
      <div className="flex items-center gap-2 text-sm" data-testid="picked-color">
        {hex ? (
          <>
            <Swatch hex={hex} /> <b>{hex}</b>
          </>
        ) : (
          <span className="text-muted-foreground">Chưa chọn màu</span>
        )}
      </div>
      <div className="flex gap-2">
        <Button className="flex-1" disabled={!hex} onClick={() => hex && onUse(hex)}>
          Dùng màu này
        </Button>
        <Button variant="outline" onClick={onBack}>
          Quay lại
        </Button>
      </div>
    </div>
  );
}

/**
 * The recognition evidence of one product. The engine uses only what is declared here (it never
 * infers from the name), so a wrong value lowers the accuracy: saving needs a ticked confirmation.
 */
export function EvidenceDialog({ productId, onClose }: { productId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const view = useQuery({ queryKey: qk.adminEvidence(productId), queryFn: () => getEvidence(productId) }).data;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [picking, setPicking] = useState<string | null>(null);

  useEffect(() => {
    if (view) {
      setDraft({
        ocr: view.evidence.ocr_keywords.join(', '),
        color: view.evidence.color_code ?? '',
        hex: '',
        force: view.evidence.force_evidence,
        confusable: view.evidence.confusable_with.join(', '),
      });
    }
  }, [view]);

  const save = useMutation({
    mutationFn: async (current: Draft) => {
      const colorCode = current.color.trim() || null;
      // the reference first: the evidence check then sees the colour it names
      if (colorCode && current.hex.trim()) await updateColor(colorCode, current.hex.trim());
      return updateEvidence(productId, {
        ocr_keywords: parseList(current.ocr),
        color_code: colorCode,
        force_evidence: current.force,
        confusable_with: parseList(current.confusable),
      });
    },
    onSuccess: (saved) => {
      const warnings = saved.warnings.length ? ` · ${saved.warnings.join('; ')}` : '';
      toast.success(`Đã lưu bằng chứng SKU ${productId}${warnings}`);
      void queryClient.invalidateQueries({ queryKey: ['admin'] });
      onClose();
    },
    onError: (error) => toast.error(errorText(error)),
  });

  const title = view ? `Bằng chứng nhận diện · SKU ${productId} — ${view.product.name}` : `Bằng chứng nhận diện · SKU ${productId}`;
  if (!view || !draft) {
    return (
      <Dialog open onClose={onClose} title={title}>
        <RoutePending />
      </Dialog>
    );
  }
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft({ ...draft, [key]: value });
  const currentColor = view.colors.find((color) => color.code === draft.color.trim().toUpperCase());

  return (
    <Dialog open onClose={onClose} title={title} className="sm:max-w-xl">
      {picking ? (
        <ColorPicker
          url={picking}
          onBack={() => setPicking(null)}
          onUse={(hex) => {
            set('hex', hex);
            setPicking(null);
          }}
        />
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            AI chỉ dùng dữ liệu khai báo ở đây (không suy từ tên). Sai bằng chứng làm giảm độ chính xác nhận diện.
          </p>
          <div className="space-y-1">
            <Label htmlFor="ev-ocr">Từ khoá OCR (chữ in trên bao bì, cách nhau bởi dấu phẩy; ≥ {view.ocr_min_length} ký tự)</Label>
            <Input id="ev-ocr" value={draft.ocr} onChange={(event) => set('ocr', event.target.value)} />
            <p className="text-xs text-muted-foreground">
              Khi so khớp chỉ giữ chữ và số: <b>BE-203</b> → <b>BE203</b>, <b>100 g</b> → <b>100G</b>. OCR đang đọc chữ Latin.
            </p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="ev-color">Mã màu {currentColor?.hex && <Swatch hex={currentColor.hex} />}</Label>
            <Input
              id="ev-color"
              list="ev-colors"
              placeholder="vd. BE203 (để trống nếu không dùng)"
              value={draft.color}
              onChange={(event) => set('color', event.target.value)}
            />
            <datalist id="ev-colors">
              {view.colors.map((color) => (
                <option key={color.code} value={color.code} />
              ))}
            </datalist>
            {currentColor?.missing && <Badge variant="warning">Mã {currentColor.code} chưa có màu tham chiếu — điểm màu = 0</Badge>}
          </div>
          <div className="space-y-1">
            <Label htmlFor="ev-hex">Màu tham chiếu cho mã trên (hex, chỉ điền khi muốn tạo/sửa)</Label>
            <Input id="ev-hex" placeholder="#RRGGBB" value={draft.hex} onChange={(event) => set('hex', event.target.value)} />
            {view.gallery.length > 0 && (
              <>
                <p className="text-xs text-muted-foreground">Hoặc chạm một ảnh để chấm màu:</p>
                <div className="flex gap-2 overflow-x-auto">
                  {view.gallery.map((url, index) => (
                    <button key={url} type="button" className="flex-none" onClick={() => setPicking(url)}>
                      <img src={url} alt={`gallery ${index + 1}`} className="h-16 w-16 rounded object-cover" />
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
          <fieldset className="space-y-1">
            <legend className="text-sm font-medium">Plugin bắt buộc chạy</legend>
            <div className="flex gap-4 text-sm">
              {PLUGINS.map(([key, label]) => (
                <label key={key} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={draft.force.includes(key)}
                    onChange={(event) =>
                      set('force', event.target.checked ? [...draft.force, key] : draft.force.filter((k) => k !== key))
                    }
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="space-y-1">
            <Label htmlFor="ev-confusable">Dễ nhầm với SKU (mã, cách nhau bởi dấu phẩy — tự ghi cả hai chiều)</Label>
            <Input id="ev-confusable" value={draft.confusable} onChange={(event) => set('confusable', event.target.value)} />
          </div>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
            {view.confirm_text}
          </label>
          <div className="flex gap-2">
            <Button className="flex-1" disabled={!confirmed || save.isPending} onClick={() => save.mutate(draft)}>
              Lưu bằng chứng
            </Button>
            <Button variant="outline" onClick={onClose}>
              Đóng
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
