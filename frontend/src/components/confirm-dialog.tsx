import { useCallback, useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';

interface Pending {
  message: string;
  okText: string;
  danger: boolean;
  resolve: (ok: boolean) => void;
}

/**
 * The app's own yes/no question (the browser's `confirm()` is ugly and blocks the page).
 * `const { confirm, dialog } = useConfirm()`, render `dialog`, then `if (await confirm('…')) …`.
 */
export function useConfirm(): {
  confirm: (message: string, okText?: string, danger?: boolean) => Promise<boolean>;
  dialog: ReactNode;
} {
  const [pending, setPending] = useState<Pending | null>(null);

  const confirm = useCallback(
    (message: string, okText = 'Đồng ý', danger = false) =>
      new Promise<boolean>((resolve) => setPending({ message, okText, danger, resolve })),
    [],
  );

  const answer = useCallback(
    (ok: boolean) => {
      pending?.resolve(ok);
      setPending(null);
    },
    [pending],
  );

  const dialog = (
    <Dialog open={pending !== null} onClose={() => answer(false)} title="Xác nhận">
      <p className="text-sm">{pending?.message}</p>
      <div className="mt-5 flex gap-2">
        <Button className="flex-1" variant={pending?.danger ? 'destructive' : 'default'} onClick={() => answer(true)}>
          {pending?.okText}
        </Button>
        <Button className="flex-1" variant="outline" onClick={() => answer(false)}>
          Không
        </Button>
      </div>
    </Dialog>
  );
  return { confirm, dialog };
}
