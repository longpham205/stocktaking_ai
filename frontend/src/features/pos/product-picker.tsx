import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { productsByBarcode, searchProducts } from '@/features/pos/api';
import { errorText } from '@/features/pos/use-order';
import type { OrderItem } from '@/features/pos/types';
import { formatVnd } from '@/lib/format';
import { qk } from '@/lib/query-keys';

interface ProductPickerProps {
  open: boolean;
  /** the flagged line being fixed; none: adding a product */
  item: OrderItem | null;
  onClose: () => void;
  onPick: (productId: string) => void;
  /** the line is right as the recognizer guessed it */
  onConfirm: (item: OrderItem) => void;
}

function useDebounced(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

/** Pick a product by name (accents optional) or by barcode: to add a line, or to fix a flagged one. */
export function ProductPicker({ open, item, onClose, onPick, onConfirm }: ProductPickerProps) {
  const [search, setSearch] = useState('');
  const [barcode, setBarcode] = useState('');
  const [message, setMessage] = useState('');
  const debounced = useDebounced(search, 250);
  const results = useQuery({
    queryKey: qk.products(debounced),
    queryFn: () => searchProducts(debounced),
    enabled: open,
  });

  useEffect(() => {
    if (!open) {
      setSearch('');
      setBarcode('');
      setMessage('');
    }
  }, [open]);

  async function lookupBarcode() {
    const code = barcode.trim();
    if (!code) return;
    try {
      const found = await productsByBarcode(code);
      if (found.length === 0) setMessage('Không có sản phẩm khớp mã này — thử tìm theo tên');
      else onPick(found[0].id);
    } catch (error) {
      setMessage(errorText(error));
    }
  }

  return (
    <Dialog open={open} onClose={onClose} variant="sheet" title={item ? 'Xác nhận sản phẩm' : 'Thêm sản phẩm'}>
      {item && (
        <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-3">
          <div className="text-xs text-muted-foreground">Hệ thống đoán</div>
          <div className="font-medium">{item.product_name}</div>
          <Button className="mt-2 w-full" onClick={() => onConfirm(item)}>
            <Check className="h-4 w-4" />
            Đúng, giữ nguyên
          </Button>
          <p className="mt-3 text-xs text-muted-foreground">Hoặc chọn sản phẩm khác:</p>
        </div>
      )}
      <div className="space-y-2">
        <Input
          placeholder="Tìm theo tên"
          aria-label="Tìm theo tên"
          autoComplete="off"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Input
          placeholder="Hoặc nhập / quét mã vạch rồi Enter"
          aria-label="Mã vạch"
          inputMode="numeric"
          autoComplete="off"
          value={barcode}
          onChange={(event) => setBarcode(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              void lookupBarcode();
            }
          }}
        />
        {message && <p className="text-sm text-destructive">{message}</p>}
      </div>
      <div className="mt-3 max-h-72 space-y-1 overflow-y-auto">
        {results.data?.map((product) => (
          <button
            key={product.id}
            type="button"
            className="flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm hover:bg-secondary"
            onClick={() => onPick(product.id)}
          >
            <span>{product.name}</span>
            <span className="text-xs text-muted-foreground">
              {product.price === null ? 'chưa có giá' : formatVnd(product.price)}
            </span>
          </button>
        ))}
        {results.isSuccess && results.data.length === 0 && (
          <p className="py-4 text-center text-sm text-muted-foreground">Không tìm thấy sản phẩm</p>
        )}
      </div>
      <Button variant="outline" className="mt-3 w-full" onClick={onClose}>
        Đóng
      </Button>
    </Dialog>
  );
}
