import type { ReactNode } from 'react';
import { Link } from '@tanstack/react-router';
import { BarChart3, Camera, History, LogOut, Package, Receipt, ScanLine, Settings, SlidersHorizontal, Users } from 'lucide-react';
import { useConfirm } from '@/components/confirm-dialog';
import { Button } from '@/components/ui/button';
import { useLogout, useMe } from '@/features/auth/use-auth';
import { getOpenOrder, voidOrder } from '@/features/pos/api';
import type { Role } from '@/lib/types';

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
}

const STAFF_NAV: NavItem[] = [
  { to: '/pos', label: 'Bán hàng', icon: <Camera className="h-4 w-4" /> },
  { to: '/history', label: 'Lịch sử', icon: <History className="h-4 w-4" /> },
];

const ADMIN_NAV: NavItem[] = [
  { to: '/admin/reports', label: 'Báo cáo', icon: <BarChart3 className="h-4 w-4" /> },
  { to: '/admin/products', label: 'Sản phẩm', icon: <Package className="h-4 w-4" /> },
  { to: '/admin/orders', label: 'Đơn hàng', icon: <Receipt className="h-4 w-4" /> },
  { to: '/admin/users', label: 'Nhân viên', icon: <Users className="h-4 w-4" /> },
  { to: '/admin/settings', label: 'Cài đặt', icon: <Settings className="h-4 w-4" /> },
  { to: '/admin/advanced', label: 'Nâng cao', icon: <SlidersHorizontal className="h-4 w-4" /> },
];

export function navFor(role: Role): NavItem[] {
  // an admin can sell too (the legacy web let them): the POS first, then the admin tabs
  return role === 'admin' ? [...STAFF_NAV, ...ADMIN_NAV] : STAFF_NAV;
}

export function AppShell({ children }: { children: ReactNode }) {
  const me = useMe().data;
  const logoutMutation = useLogout();
  const { confirm, dialog } = useConfirm();

  /** Ends the shift. An unpaid basket is voided first, after asking (it would stay open otherwise). */
  async function logout() {
    const open = await getOpenOrder().catch(() => null);
    if (open && open.items.length > 0) {
      if (!(await confirm('Đơn hiện tại chưa thanh toán. Huỷ đơn và đóng ca?', 'Huỷ đơn và đóng ca', true))) return;
      await voidOrder(open.id).catch(() => undefined);
    }
    logoutMutation.mutate();
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-20 border-b border-border bg-background/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <div className="flex flex-wrap items-center gap-4">
            <span className="flex items-center gap-2 text-sm font-semibold">
              <ScanLine className="h-4.5 w-4.5 text-primary" aria-hidden="true" />
              Stocktaking POS
            </span>
            <nav className="flex flex-wrap items-center gap-1 text-sm" aria-label="Điều hướng chính">
              {me &&
                navFor(me.user.role).map((item) => (
                  <Link
                    key={item.to}
                    to={item.to}
                    className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground [&.active]:bg-secondary [&.active]:text-foreground"
                    activeProps={{ className: 'active' }}
                  >
                    {item.icon}
                    {item.label}
                  </Link>
                ))}
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm">
            {me && <span className="text-muted-foreground">{me.user.full_name || me.user.username}</span>}
            <Button variant="ghost" size="sm" onClick={() => void logout()} disabled={logoutMutation.isPending}>
              <LogOut className="h-4 w-4" />
              Đăng xuất
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>
      {dialog}
    </div>
  );
}
