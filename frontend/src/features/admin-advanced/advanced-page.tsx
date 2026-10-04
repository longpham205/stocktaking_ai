import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { History, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { RoutePending } from '@/components/route-states';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Dialog } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { PasswordInput } from '@/components/ui/password-input';
import { Label } from '@/components/ui/label';
import { getChangeLog, revertChange } from '@/features/admin-products/api';
import { percent, versusBaseline } from '@/features/admin-products/lib';
import { errorText } from '@/features/pos/use-order';
import { apiFetch } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';
import { qk } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

type ConfigValue = number | boolean | string | null;

/** One registry entry (backend engine_config/registry.py) with its YAML and current values. */
export interface ConfigItem {
  key: string;
  label: string;
  group: string;
  /** readonly: shown only; reload: applied by rebuilding the pipeline */
  tier: 'readonly' | 'reload';
  type: 'float' | 'int' | 'bool' | 'str';
  help?: string;
  min?: number;
  max?: number;
  default: ConfigValue;
  value: ConfigValue;
  overridden: boolean;
}

export interface EngineConfig {
  items: ConfigItem[];
  reloading: boolean;
  config_error: string | null;
  advanced_password_set: boolean;
  pipeline_config: string;
  applied?: boolean | null;
}

interface Metrics {
  f1?: number | null;
  fusion_accuracy?: number | null;
}

export interface Validation {
  status: 'idle' | 'running' | 'done' | 'error';
  started_at?: string | null;
  finished_at?: string | null;
  by?: string | null;
  result?: Metrics | null;
  baseline?: Metrics | null;
  error?: string | null;
}

/** {dotted key: new value}; null goes back to the YAML's value. */
type Changes = Record<string, ConfigValue>;

const show = (value: ConfigValue | undefined) =>
  value === true ? 'bật' : value === false ? 'tắt' : value === null || value === undefined ? '—' : String(value);

function PasswordField({ value, onChange, label = 'Mật khẩu nâng cao' }: { value: string; onChange: (v: string) => void; label?: string }) {
  return (
    <div className="space-y-1">
      <Label htmlFor="advanced-password">{label}</Label>
      <PasswordInput id="advanced-password" autoComplete="off" value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

interface ApplyDialogProps {
  changes: Changes;
  items: ConfigItem[];
  onClose: () => void;
  /** applied: what the admin typed is now the value in use */
  onApplied: () => void;
}

function ApplyDialog({ changes, items, onClose, onApplied }: ApplyDialogProps) {
  const queryClient = useQueryClient();
  const [password, setPassword] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const byKey = new Map(items.map((item) => [item.key, item]));
  const apply = useMutation({
    mutationFn: () =>
      apiFetch<EngineConfig>('/api/admin/config/apply', {
        method: 'POST',
        body: { changes, advanced_password: password, confirm: true },
      }),
    onSuccess: (result) => {
      toast.success(result.applied ? 'Đã áp dụng — nên chạy kiểm định để kiểm tra độ chính xác' : 'Không có gì thay đổi');
      queryClient.setQueryData(qk.adminConfig(), result);
      onApplied();
      onClose();
    },
    onError: (error) => toast.error(errorText(error)),
  });

  return (
    <Dialog open onClose={onClose} title="Áp dụng thiết lập nâng cao">
      <Card className="space-y-1 p-3 text-sm">
        {Object.entries(changes).map(([key, value]) => {
          const item = byKey.get(key);
          return (
            <div key={key} data-testid="pending-change">
              <b>{item?.label ?? key}</b>: {show(item?.value)} →{' '}
              <b>{value === null ? `giá trị gốc (${show(item?.default)})` : show(value)}</b>
            </div>
          );
        })}
      </Card>
      <p className="mt-3 text-xs text-amber-700">
        Hệ thống sẽ ngừng nhận diện khoảng 30–60 giây để nạp lại. Thay đổi có thể làm giảm độ chính xác — nên chạy kiểm định
        sau khi áp dụng.
      </p>
      <div className="mt-3 space-y-3">
        <PasswordField value={password} onChange={setPassword} />
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
          Tôi hiểu thay đổi này ảnh hưởng nhận diện và tạm dừng hệ thống
        </label>
        <div className="flex gap-2">
          <Button className="flex-1" disabled={!confirmed || apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending ? 'Đang nạp lại pipeline…' : 'Áp dụng'}
          </Button>
          <Button variant="outline" onClick={onClose}>
            Huỷ
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function ConfigHistoryDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [password, setPassword] = useState('');
  const entries = useQuery({ queryKey: qk.adminChangeLog('config'), queryFn: () => getChangeLog('config') });
  const revert = useMutation({
    mutationFn: (entryId: number) => revertChange(entryId, password),
    onSuccess: () => {
      toast.success('Đã hoàn tác');
      void queryClient.invalidateQueries({ queryKey: ['admin'] });
      onClose();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const stored = (value: string | null) => (value === null ? 'gốc' : show(JSON.parse(value) as ConfigValue));

  return (
    <Dialog open onClose={onClose} title="Lịch sử thiết lập nâng cao" className="sm:max-w-xl">
      <PasswordField value={password} onChange={setPassword} label="Mật khẩu nâng cao (để hoàn tác)" />
      <div className="mt-3 space-y-3">
        {entries.isPending ? (
          <RoutePending />
        ) : entries.data?.length ? (
          entries.data.map((entry) => (
            <div key={entry.id} className="flex items-center gap-3 text-sm" data-testid="config-entry">
              <div className="min-w-0 flex-1">
                <b>{entry.field}</b>: {stored(entry.old)} → <b>{stored(entry.new)}</b>
                <div className="text-xs text-muted-foreground">
                  {entry.by ?? 'máy chủ'} · {formatDateTime(entry.at)}
                </div>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={revert.isPending}
                onClick={() => (password ? revert.mutate(entry.id) : toast.error('Nhập mật khẩu nâng cao trước'))}
              >
                Hoàn tác
              </Button>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">Chưa có thay đổi nào.</p>
        )}
      </div>
      <Button variant="outline" className="mt-4 w-full" onClick={onClose}>
        Đóng
      </Button>
    </Dialog>
  );
}

function ValidationCard({ validation }: { validation: Validation }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [password, setPassword] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const baseline = validation.baseline ?? {};
  const result = validation.result ?? {};
  const start = useMutation({
    mutationFn: () =>
      apiFetch<Validation>('/api/admin/validation', { method: 'POST', body: { confirm: true, advanced_password: password } }),
    onSuccess: (started) => {
      toast.success('Đã bắt đầu kiểm định');
      queryClient.setQueryData(qk.adminValidation(), started);
      setAsking(false);
      setPassword('');
      setConfirmed(false);
    },
    onError: (error) => toast.error(errorText(error)),
  });

  const versus = (current: number | null | undefined, base: number | null | undefined) => {
    const text = versusBaseline(current, base);
    return text ? <span className={cn((current ?? 0) >= (base ?? 0) ? 'text-emerald-600' : 'text-amber-600')}> ({text})</span> : null;
  };

  return (
    <section className="space-y-2">
      <h2 className="text-base font-semibold">Kiểm định độ chính xác</h2>
      <Card className="space-y-1 p-3 text-sm" data-testid="validation">
        {validation.status === 'running' ? (
          <div>
            ⏳ Đang kiểm định (bắt đầu {validation.started_at ? new Date(validation.started_at).toLocaleTimeString('vi-VN') : '?'} bởi{' '}
            {validation.by ?? '?'}) — thu ngân tạm không chụp được.
          </div>
        ) : validation.status === 'done' ? (
          <>
            <div>✅ Xong lúc {validation.finished_at ? formatDateTime(validation.finished_at) : '?'}</div>
            <div>
              F1 end-to-end: <b>{percent(result.f1)}</b>
              {versus(result.f1, baseline.f1)}
            </div>
            <div>
              Độ chính xác sau hợp nhất: <b>{percent(result.fusion_accuracy)}</b>
              {versus(result.fusion_accuracy, baseline.fusion_accuracy)}
            </div>
          </>
        ) : validation.status === 'error' ? (
          <div className="text-destructive">❌ Lỗi: {validation.error}</div>
        ) : (
          <div className="text-muted-foreground">Chưa chạy.</div>
        )}
        <div className="text-xs text-muted-foreground">
          {baseline.f1 === null || baseline.f1 === undefined
            ? 'Chưa có baseline để so.'
            : `Baseline: F1 ${percent(baseline.f1)} · sau hợp nhất ${percent(baseline.fusion_accuracy)}`}
        </div>
      </Card>
      <Button variant="outline" className="w-full" disabled={validation.status === 'running'} onClick={() => setAsking(true)}>
        Chạy kiểm định…
      </Button>
      <Dialog open={asking} onClose={() => setAsking(false)} title="Kiểm định độ chính xác">
        <p className="text-xs text-amber-700">
          Chạy toàn bộ benchmark bằng pipeline đang dùng. Trong lúc chạy (có thể 20–50 phút với model thật){' '}
          <b>thu ngân không chụp nhận diện được</b> — chỉ chạy ngoài giờ bán.
        </p>
        <div className="mt-3 space-y-3">
          <PasswordField value={password} onChange={setPassword} />
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
            Tôi hiểu hệ thống ngừng nhận diện trong lúc kiểm định
          </label>
          <div className="flex gap-2">
            <Button className="flex-1" disabled={!confirmed || start.isPending} onClick={() => start.mutate()}>
              Bắt đầu
            </Button>
            <Button variant="outline" onClick={() => setAsking(false)}>
              Huỷ
            </Button>
          </div>
        </div>
      </Dialog>
    </section>
  );
}

/**
 * The engine settings an admin may change from the web (🟡: applied with the advanced password by
 * rebuilding the pipeline; 🔒: shown only) and the benchmark validation that checks the result.
 */
export function AdvancedPage() {
  const config = useQuery({ queryKey: qk.adminConfig(), queryFn: () => apiFetch<EngineConfig>('/api/admin/config') }).data;
  const validation = useQuery({
    queryKey: qk.adminValidation(),
    queryFn: () => apiFetch<Validation>('/api/admin/validation'),
    // a running validation is followed until it ends
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 5000 : false),
  }).data;
  const [edits, setEdits] = useState<Changes>({});
  // number fields keep what is typed (an empty field while retyping must stay empty)
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Changes | null>(null);
  const [history, setHistory] = useState(false);

  if (!config) return <RoutePending />;
  const groups = new Map<string, ConfigItem[]>();
  for (const item of config.items) groups.set(item.group, [...(groups.get(item.group) ?? []), item]);

  /** What the admin typed that differs from the value in use. */
  function changed(): Changes {
    const out: Changes = {};
    for (const item of config!.items) {
      if (item.tier !== 'reload') continue;
      if (item.type === 'bool') {
        if (item.key in edits && edits[item.key] !== item.value) out[item.key] = edits[item.key];
        continue;
      }
      const text = typed[item.key]?.trim();
      if (text === undefined || text === '' || Number.isNaN(Number(text))) continue; // untouched or unusable: no change
      if (Number(text) !== item.value) out[item.key] = Number(text);
    }
    return out;
  }

  function askApply(changes: Changes) {
    if (Object.keys(changes).length === 0) toast('Không có thay đổi nào');
    else setPending(changes);
  }

  function control(item: ConfigItem) {
    if (item.tier !== 'reload') return <b>{show(item.value)}</b>;
    const current = item.key in edits ? edits[item.key] : item.value;
    if (item.type === 'bool') {
      return (
        <input
          type="checkbox"
          aria-label={item.label}
          checked={current === true}
          onChange={(event) => setEdits({ ...edits, [item.key]: event.target.checked })}
        />
      );
    }
    return (
      <Input
        type="number"
        className="w-28"
        aria-label={item.label}
        step={item.type === 'int' ? 1 : 0.01}
        min={item.min}
        max={item.max}
        value={typed[item.key] ?? (current === null ? '' : String(current))}
        onChange={(event) => setTyped({ ...typed, [item.key]: event.target.value })}
      />
    );
  }

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-semibold">
        Nâng cao <span className="text-sm font-normal text-muted-foreground">({config.pipeline_config})</span>
      </h1>
      <Card className="border-amber-300 bg-amber-50 p-3 text-sm">
        🟡 <b>Cần áp dụng</b>: ảnh hưởng độ chính xác nhận diện; cần <b>mật khẩu nâng cao</b>, khi áp dụng hệ thống{' '}
        <b>ngừng nhận diện ~30–60 giây</b> để nạp lại. Sau khi đổi nên chạy kiểm định. 🔒 <b>Chỉ xem</b>: đổi trong file
        config rồi chạy lại cổng kiểm định. Ngưỡng theo lượt chụp và cài đặt quầy ở tab Cài đặt.
      </Card>
      {config.config_error && (
        <Card className="border-red-300 bg-red-50 p-3 text-sm" role="alert">
          ⚠ Thiết lập đã lưu không còn hợp lệ với file config: {config.config_error}
        </Card>
      )}
      {!config.advanced_password_set && (
        <Card className="border-red-300 bg-red-50 p-3 text-sm" role="alert">
          Chưa có mật khẩu nâng cao — chạy trên máy chủ: <code>make reset-advanced-password</code>
        </Card>
      )}
      {config.reloading && <Card className="border-amber-300 bg-amber-50 p-3 text-sm">⏳ Đang nạp lại pipeline…</Card>}

      {[...groups.entries()].map(([group, items]) => (
        <section key={group} className="space-y-1">
          <h2 className="text-base font-semibold">{group}</h2>
          <div className="divide-y divide-border rounded-lg border border-border">
            {items.map((item) => (
              <div key={item.key} className="flex flex-wrap items-center justify-between gap-3 p-3" data-testid={`config-${item.key}`}>
                <div className="min-w-52 flex-1 space-y-0.5">
                  <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                    {item.label}
                    {item.tier === 'reload' ? <Badge variant="warning">cần áp dụng</Badge> : <Badge variant="muted">🔒 chỉ xem</Badge>}
                    {item.overridden && <Badge variant="warning">đã sửa · gốc: {show(item.default)}</Badge>}
                  </div>
                  {item.help && <div className="text-xs text-muted-foreground">{item.help}</div>}
                  <div className="text-xs text-muted-foreground">
                    {item.key}
                    {item.tier === 'reload' && item.type !== 'bool' && ` · ${item.min}–${item.max}`}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {control(item)}
                  {item.overridden && (
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-8 w-8"
                      title="Về giá trị gốc"
                      aria-label={`Về giá trị gốc: ${item.label}`}
                      onClick={() => askApply({ ...changed(), [item.key]: null })}
                    >
                      <RotateCcw className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}

      <div className="flex gap-2">
        <Button className="flex-1" disabled={config.reloading} onClick={() => askApply(changed())}>
          Áp dụng thay đổi…
        </Button>
        <Button variant="outline" onClick={() => setHistory(true)}>
          <History className="h-4 w-4" />
          Lịch sử
        </Button>
      </div>

      {validation && <ValidationCard validation={validation} />}
      {pending && (
        <ApplyDialog
          changes={pending}
          items={config.items}
          onClose={() => setPending(null)}
          onApplied={() => {
            setEdits({});
            setTyped({});
          }}
        />
      )}
      {history && <ConfigHistoryDialog onClose={() => setHistory(false)} />}
    </div>
  );
}
