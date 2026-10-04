import { useState, type KeyboardEvent } from 'react';
import { Minus, Plus, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { parseMoney } from '@/features/pos/lib';
import type { OrderItem } from '@/features/pos/types';
import { formatVnd } from '@/lib/format';
import { cn } from '@/lib/utils';

interface LineItemProps {
  item: OrderItem;
  focused: boolean;
  onOpen: () => void;
  onQuantity: (quantity: number) => void;
  onPrice: (price: number) => void;
}

/** One order line. A flagged line (amber) opens the picker; a line without a price takes one typed in. */
export function LineItem({ item, focused, onOpen, onQuantity, onPrice }: LineItemProps) {
  const [typed, setTyped] = useState('');
  const commitPrice = () => {
    const price = parseMoney(typed);
    if (price !== null) onPrice(price);
  };

  return (
    <div
      data-testid={`line-${item.id}`}
      data-line-id={item.id}
      className={cn(
        'flex cursor-pointer items-center gap-3 rounded-lg border bg-card p-2 transition-colors',
        item.flagged ? 'border-amber-400 bg-amber-50' : 'border-border',
        focused && 'ring-2 ring-primary',
      )}
      onClick={onOpen}
    >
      {item.thumbnail_url ? (
        <img src={item.thumbnail_url} alt="" className="h-12 w-12 flex-none rounded object-cover" />
      ) : (
        <div className="flex h-12 w-12 flex-none items-center justify-center rounded bg-muted text-[10px] text-muted-foreground">
          IMG
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1 truncate text-sm font-medium">
          {item.flagged && <TriangleAlert className="h-4 w-4 flex-none text-amber-500" aria-label="Cần xác nhận" />}
          {item.product_name}
        </div>
        {item.price_missing ? (
          <Input
            className="mt-1 h-8 w-32 border-amber-400"
            inputMode="numeric"
            placeholder="Nhập giá"
            aria-label={`Giá ${item.product_name}`}
            data-price-input
            value={typed}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => setTyped(event.target.value)}
            onBlur={commitPrice}
            onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
              if (event.key === 'Enter') event.currentTarget.blur();
            }}
          />
        ) : (
          <div className="text-xs text-muted-foreground">
            {formatVnd(item.unit_price ?? 0)}
            {item.manual_price && ' (giá tay)'}
          </div>
        )}
      </div>
      <div className="flex flex-none items-center gap-1" onClick={(event) => event.stopPropagation()}>
        <Button size="icon" variant="outline" className="h-8 w-8" aria-label="Giảm" onClick={() => onQuantity(item.quantity - 1)}>
          <Minus className="h-3.5 w-3.5" />
        </Button>
        <span className="w-6 text-center text-sm font-semibold" aria-label="Số lượng">
          {item.quantity}
        </span>
        <Button size="icon" variant="outline" className="h-8 w-8" aria-label="Tăng" onClick={() => onQuantity(item.quantity + 1)}>
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}
