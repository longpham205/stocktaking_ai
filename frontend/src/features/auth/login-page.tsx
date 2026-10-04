import { useState, type FormEvent } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { ScanLine } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';
import { useLogin } from '@/features/auth/use-auth';
import { ApiError } from '@/lib/api-client';

function failure(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Không kết nối được máy chủ — kiểm tra mạng';
  const wait = error.extra.retry_after;
  if (error.code === 'RATE_LIMITED' && typeof wait === 'number') {
    return `Nhập sai quá nhiều lần, thử lại sau ${Math.ceil(wait / 60)} phút`;
  }
  return error.message;
}

export function LoginPage() {
  const navigate = useNavigate();
  const loginMutation = useLogin();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  function submit(event: FormEvent) {
    event.preventDefault();
    loginMutation.mutate({ username, password }, { onSuccess: () => navigate({ to: '/' }) });
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-sm space-y-6 p-6">
        <div className="space-y-1 text-center">
          <ScanLine className="mx-auto h-8 w-8 text-primary" aria-hidden="true" />
          <h1 className="text-xl font-semibold">Stocktaking POS</h1>
          <p className="text-sm text-muted-foreground">Đăng nhập để bắt đầu ca làm việc</p>
        </div>
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-2">
            <Label htmlFor="username">Tài khoản</Label>
            <Input
              id="username"
              autoComplete="username"
              autoCapitalize="none"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Mật khẩu</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>
          {loginMutation.isError && (
            <p role="alert" className="text-sm text-destructive">
              {failure(loginMutation.error)}
            </p>
          )}
          <Button type="submit" className="w-full" disabled={loginMutation.isPending}>
            {loginMutation.isPending && <Spinner />}
            Đăng nhập
          </Button>
        </form>
      </Card>
    </div>
  );
}
