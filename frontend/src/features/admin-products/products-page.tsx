import { useEffect, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Brain, ChevronLeft, ChevronRight, FlaskConical, History } from 'lucide-react';
import { toast } from 'sonner';
import { RoutePending } from '@/components/route-states';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getAdminProducts, updateProduct, type ProductChange, type ProductFilter } from '@/features/admin-products/api';
import { EvidenceDialog } from '@/features/admin-products/evidence-dialog';
import { EvidenceTestDialog } from '@/features/admin-products/evidence-test-dialog';
import { ProductHistoryDialog } from '@/features/admin-products/product-history-dialog';
import { parseMoney } from '@/features/pos/lib';
import type { Product } from '@/features/pos/types';
import { errorText } from '@/features/pos/use-order';
import { qk } from '@/lib/query-keys';

/** How long the search waits after the last key before asking the server. */
const SEARCH_DELAY_MS = 350;

interface RowProps {
  product: Product;
  saving: boolean;
  onSave: (change: ProductChange) => void;
  onEvidence: () => void;
  onHistory: () => void;
}

function ProductRow({ product, saving, onSave, onEvidence, onHistory }: RowProps) {
  const [name, setName] = useState(product.name);
  const [barcode, setBarcode] = useState(product.barcode);
  const [price, setPrice] = useState(product.price === null ? '' : String(product.price));
  useEffect(() => {
    setName(product.name);
    setBarcode(product.barcode);
    setPrice(product.price === null ? '' : String(product.price));
  }, [product]);

  function save() {
    const change: ProductChange = { price: parseMoney(price), barcode: barcode.trim() };
    const renamed = name.trim();
    if (renamed && renamed !== product.name) change.name = renamed; // the name only when it really changed
    onSave(change);
  }

  return (
    <tr className="border-b border-border align-top" data-testid={`product-${product.id}`}>
      <td className="py-2 pr-2 font-medium">{product.id}</td>
      <td className="py-2 pr-2">
        <Input aria-label={`Tên ${product.id}`} value={name} onChange={(event) => setName(event.target.value)} />
        <div className="mt-1 flex flex-wrap gap-1">
          {product.needs_naming && <Badge variant="warning">chưa đặt tên</Badge>}
          {product.missing_color_reference && <Badge variant="warning">thiếu màu tham chiếu</Badge>}
        </div>
      </td>
      <td className="py-2 pr-2">
        <Input
          className="w-40"
          aria-label={`Barcode ${product.id}`}
          placeholder="—"
          value={barcode}
          onChange={(event) => setBarcode(event.target.value)}
        />
      </td>
      <td className="py-2 pr-2">
        <Input
          className="w-28"
          aria-label={`Giá ${product.id}`}
          inputMode="numeric"
          placeholder="chưa có giá"
          value={price}
          onChange={(event) => setPrice(event.target.value)}
        />
      </td>
      <td className="py-2">
        <div className="flex gap-1">
          <Button size="sm" disabled={saving} onClick={save}>
            Lưu
          </Button>
          <Button size="icon" variant="outline" className="h-8 w-8" title="Bằng chứng nhận diện (AI)" aria-label={`Bằng chứng ${product.id}`} onClick={onEvidence}>
            <Brain className="h-4 w-4" />
          </Button>
          <Button size="icon" variant="outline" className="h-8 w-8" title="Lịch sử thay đổi" aria-label={`Lịch sử ${product.id}`} onClick={onHistory}>
            <History className="h-4 w-4" />
          </Button>
        </div>
      </td>
    </tr>
  );
}

type Opened = { kind: 'evidence' | 'history'; productId: string } | { kind: 'test' } | null;

/** The catalog on sale: names, barcodes, prices, and each product's recognition evidence and history. */
export function ProductsPage() {
  const queryClient = useQueryClient();
  const [typed, setTyped] = useState('');
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<ProductFilter>('');
  const [page, setPage] = useState(1);
  const [opened, setOpened] = useState<Opened>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(typed);
      setPage(1);
    }, SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [typed]);

  const products = useQuery({
    queryKey: qk.adminProducts(search, filter, page),
    queryFn: () => getAdminProducts(search, filter, page),
    placeholderData: keepPreviousData, // the table stays while the next page or search loads
  }).data;
  const save = useMutation({
    mutationFn: ({ productId, change }: { productId: string; change: ProductChange }) => updateProduct(productId, change),
    onSuccess: (saved) => {
      toast.success(`Đã lưu sản phẩm ${saved.id}`);
      void queryClient.invalidateQueries({ queryKey: ['admin', 'products'] });
    },
    onError: (error) => toast.error(errorText(error)),
  });

  if (!products) return <RoutePending />;
  const pages = Math.max(1, Math.ceil(products.total / products.size));
  const chips: [ProductFilter, string][] = [
    ['', filter === '' ? `Tất cả (${products.total})` : 'Tất cả'],
    ['missing_price', `Thiếu giá (${products.missing_price})`],
    ['missing_barcode', `Thiếu barcode (${products.missing_barcode})`],
    ['needs_naming', `Chưa đặt tên (${products.needs_naming})`],
  ];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Sản phẩm</h1>
        <Button variant="outline" onClick={() => setOpened({ kind: 'test' })}>
          <FlaskConical className="h-4 w-4" />
          Thử bằng chứng
        </Button>
      </div>
      <Input
        placeholder="Tìm theo tên / mã / barcode"
        aria-label="Tìm sản phẩm"
        value={typed}
        onChange={(event) => setTyped(event.target.value)}
      />
      <div className="flex flex-wrap gap-1">
        {chips.map(([value, label]) => (
          <Button
            key={value}
            size="sm"
            variant={filter === value ? 'default' : 'outline'}
            onClick={() => {
              setFilter(value);
              setPage(1);
            }}
          >
            {label}
          </Button>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th className="py-2 font-medium">Mã</th>
              <th className="py-2 font-medium">Tên</th>
              <th className="py-2 font-medium">Barcode</th>
              <th className="py-2 font-medium">Giá (đ)</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {products.items.map((product) => (
              <ProductRow
                key={product.id}
                product={product}
                saving={save.isPending}
                onSave={(change) => save.mutate({ productId: product.id, change })}
                onEvidence={() => setOpened({ kind: 'evidence', productId: product.id })}
                onHistory={() => setOpened({ kind: 'history', productId: product.id })}
              />
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-2">
        <Button size="icon" variant="outline" aria-label="Trang trước" disabled={page <= 1} onClick={() => setPage(page - 1)}>
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <span className="text-sm text-muted-foreground">
          Trang {page}/{pages}
        </span>
        <Button size="icon" variant="outline" aria-label="Trang sau" disabled={page >= pages} onClick={() => setPage(page + 1)}>
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
      {opened?.kind === 'evidence' && <EvidenceDialog productId={opened.productId} onClose={() => setOpened(null)} />}
      {opened?.kind === 'history' && <ProductHistoryDialog productId={opened.productId} onClose={() => setOpened(null)} />}
      {opened?.kind === 'test' && <EvidenceTestDialog onClose={() => setOpened(null)} />}
    </div>
  );
}
