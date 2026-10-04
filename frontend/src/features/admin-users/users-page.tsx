import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { RoutePending } from '@/components/route-states';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Dialog } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { PasswordInput } from '@/components/ui/password-input';
import { errorText } from '@/features/pos/use-order';
import { apiFetch } from '@/lib/api-client';
import { qk } from '@/lib/query-keys';
import type { Role } from '@/lib/types';

/** Mirrors backend users/schemas.py `StaffOut`. */
export interface Staff {
  id: number;
  username: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  /** has an open shift */
  online: boolean;
}

const MIN_PASSWORD = 8;

function ResetPasswordDialog({ staff, onClose, onSave }: { staff: Staff; onClose: () => void; onSave: (password: string) => void }) {
  const [password, setPassword] = useState('');
  const [again, setAgain] = useState('');
  const [problem, setProblem] = useState('');

  function submit(event: FormEvent) {
    event.preventDefault();
    if (password.length < MIN_PASSWORD) setProblem(`Mật khẩu cần tối thiểu ${MIN_PASSWORD} ký tự`);
    else if (password !== again) setProblem('Hai lần nhập không khớp');
    else onSave(password);
  }

  return (
    <Dialog open onClose={onClose} title={`Đặt lại mật khẩu cho ${staff.username}`}>
      <form className="space-y-3" onSubmit={submit}>
        <PasswordInput
          autoComplete="new-password"
          placeholder={`Mật khẩu mới (tối thiểu ${MIN_PASSWORD} ký tự)`}
          aria-label="Mật khẩu mới"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <PasswordInput
          autoComplete="new-password"
          placeholder="Nhập lại mật khẩu mới"
          aria-label="Nhập lại mật khẩu mới"
          value={again}
          onChange={(event) => setAgain(event.target.value)}
        />
        {problem && (
          <p role="alert" className="text-sm text-destructive">
            {problem}
          </p>
        )}
        <div className="flex gap-2">
          <Button type="submit" className="flex-1">
            Đặt lại
          </Button>
          <Button type="button" variant="outline" className="flex-1" onClick={onClose}>
            Huỷ
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Staff accounts: add one, reset a password, lock or unlock. A lock or a new password ends the open shift. */
export function UsersPage() {
  const queryClient = useQueryClient();
  const staff = useQuery({
    queryKey: qk.adminUsers(),
    queryFn: async () => (await apiFetch<{ items: Staff[] }>('/api/admin/users')).items,
  });
  const [form, setForm] = useState({ username: '', full_name: '', password: '', role: 'staff' as Role });
  const [resetting, setResetting] = useState<Staff | null>(null);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: qk.adminUsers() });
  const onError = (error: unknown) => {
    toast.error(errorText(error));
  };

  const create = useMutation({
    mutationFn: () => apiFetch<Staff>('/api/admin/users', { method: 'POST', body: form }),
    onSuccess: () => {
      toast.success('Đã thêm nhân viên');
      setForm({ username: '', full_name: '', password: '', role: 'staff' });
      refresh();
    },
    onError,
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { password?: string; is_active?: boolean } }) =>
      apiFetch<Staff>(`/api/admin/users/${id}`, { method: 'PATCH', body }),
    onSuccess: (_, { body }) => {
      if (body.password) {
        toast.success('Đã đặt lại mật khẩu');
        setResetting(null);
      }
      refresh();
    },
    onError,
  });

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Nhân viên</h1>
      <Card className="p-3">
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <Input
            className="w-40"
            placeholder="Tài khoản"
            aria-label="Tài khoản"
            autoCapitalize="none"
            value={form.username}
            onChange={(event) => setForm({ ...form, username: event.target.value })}
          />
          <Input
            className="min-w-40 flex-1"
            placeholder="Họ tên"
            aria-label="Họ tên"
            value={form.full_name}
            onChange={(event) => setForm({ ...form, full_name: event.target.value })}
          />
          <PasswordInput
            className="w-44"
            autoComplete="new-password"
            placeholder={`Mật khẩu (≥${MIN_PASSWORD})`}
            aria-label="Mật khẩu"
            value={form.password}
            onChange={(event) => setForm({ ...form, password: event.target.value })}
          />
          <select
            aria-label="Vai trò"
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
            value={form.role}
            onChange={(event) => setForm({ ...form, role: event.target.value as Role })}
          >
            <option value="staff">staff</option>
            <option value="admin">admin</option>
          </select>
          <Button type="submit" disabled={create.isPending}>
            Thêm
          </Button>
        </form>
      </Card>

      {staff.isPending ? (
        <RoutePending />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="py-2 font-medium">Tài khoản</th>
                <th className="py-2 font-medium">Họ tên</th>
                <th className="py-2 font-medium">Vai trò</th>
                <th className="py-2 font-medium">Trạng thái</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {staff.data?.map((member) => (
                <tr key={member.id} className="border-b border-border" data-testid={`staff-${member.username}`}>
                  <td className="py-2 font-medium">{member.username}</td>
                  <td className="py-2">{member.full_name}</td>
                  <td className="py-2">{member.role}</td>
                  <td className="py-2">{!member.is_active ? '⛔ Đã khoá' : member.online ? '🟢 Đang trong ca' : 'Hoạt động'}</td>
                  <td className="py-2">
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="outline" onClick={() => setResetting(member)}>
                        Đặt lại MK
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={patch.isPending}
                        onClick={() => patch.mutate({ id: member.id, body: { is_active: !member.is_active } })}
                      >
                        {member.is_active ? 'Khoá' : 'Mở khoá'}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {resetting && (
        <ResetPasswordDialog
          staff={resetting}
          onClose={() => setResetting(null)}
          onSave={(password) => patch.mutate({ id: resetting.id, body: { password } })}
        />
      )}
    </div>
  );
}
