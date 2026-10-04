import { useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  /** `sheet`: slides up from the bottom on a phone (the POS pickers); `center`: a plain dialog. */
  variant?: 'center' | 'sheet';
  className?: string;
  children: ReactNode;
}

/** A modal over the page: Escape or a tap outside closes it. No dependency: a portal and Tailwind. */
export function Dialog({ open, onClose, title, variant = 'center', className, children }: DialogProps) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return createPortal(
    <div
      className={cn(
        'fixed inset-0 z-50 flex justify-center bg-black/40 p-0 sm:items-center sm:p-4',
        variant === 'sheet' ? 'items-end' : 'items-center p-4',
      )}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          'max-h-[90vh] w-full overflow-y-auto bg-card p-5 text-card-foreground shadow-xl sm:max-w-md sm:rounded-xl',
          variant === 'sheet' ? 'rounded-t-xl' : 'max-w-md rounded-xl',
          className,
        )}
      >
        {title && <h2 className="mb-3 text-lg font-semibold">{title}</h2>}
        {children}
      </div>
    </div>,
    document.body,
  );
}
