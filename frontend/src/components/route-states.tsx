import { Link, type ErrorComponentProps } from '@tanstack/react-router';
import { buttonVariants } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import { ApiError } from '@/lib/api-client';

/** What a route shows while it loads, when it fails, and for an unknown address. */

export function RoutePending() {
  return (
    <div className="flex justify-center py-16 text-muted-foreground">
      <Spinner className="h-6 w-6" />
    </div>
  );
}

export function RouteError({ error, reset }: ErrorComponentProps) {
  const message = error instanceof ApiError ? error.message : 'Có lỗi xảy ra khi tải trang';
  return (
    <div className="space-y-3 py-16 text-center">
      <p className="text-lg font-semibold">Không tải được trang</p>
      <p className="text-sm text-muted-foreground">{message}</p>
      <button type="button" className={buttonVariants({ variant: 'outline' })} onClick={reset}>
        Thử lại
      </button>
    </div>
  );
}

export function RouteNotFound() {
  return (
    <div className="space-y-3 py-16 text-center">
      <p className="text-lg font-semibold">Không có trang này</p>
      <p className="text-sm text-muted-foreground">Địa chỉ không đúng hoặc trang đã được chuyển.</p>
      <Link to="/" className={buttonVariants({ variant: 'outline' })}>
        Về trang chính
      </Link>
    </div>
  );
}
