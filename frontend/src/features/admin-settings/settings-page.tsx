import { useEffect, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { RoutePending } from '@/components/route-states';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { meQuery } from '@/features/auth/use-auth';
import { errorText } from '@/features/pos/use-order';
import { apiFetch } from '@/lib/api-client';
import { qk } from '@/lib/query-keys';
import type { PosSettings } from '@/lib/types';

function Row({ title, help, children }: { title: string; help: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-4">
      <div className="min-w-52 flex-1">
        <div className="font-medium">{title}</div>
        <div className="text-sm text-muted-foreground">{help}</div>
      </div>
      {children}
    </div>
  );
}

interface ThresholdProps {
  label: string;
  /** null: the engine's own value */
  value: number | null;
  onChange: (value: number | null) => void;
  min: number;
  max: number;
  /** where the slider starts when the admin stops using the default */
  start: number;
}

function Threshold({ label, value, onChange, min, max, start }: ThresholdProps) {
  return (
    <div className="w-60 space-y-1">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={value === null} onChange={(event) => onChange(event.target.checked ? null : start)} />
        Dùng mặc định của hệ thống
      </label>
      <input
        type="range"
        className="w-full"
        aria-label={label}
        min={min}
        max={max}
        step={0.01}
        disabled={value === null}
        value={value ?? start}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <div className="text-xs text-muted-foreground">{value === null ? 'mặc định' : value.toFixed(2)}</div>
    </div>
  );
}

/** Settings that apply at once (the next capture, the next checkout). Each change is logged. */
export function SettingsPage() {
  const queryClient = useQueryClient();
  const stored = useQuery({ queryKey: qk.settings(), queryFn: () => apiFetch<PosSettings>('/api/settings') }).data;
  const [draft, setDraft] = useState<PosSettings | null>(null);
  useEffect(() => {
    if (stored) setDraft(stored);
  }, [stored]);

  const save = useMutation({
    mutationFn: (body: Partial<PosSettings>) => apiFetch<PosSettings>('/api/admin/settings', { method: 'PATCH', body }),
    onSuccess: (saved) => {
      queryClient.setQueryData(qk.settings(), saved);
      void queryClient.invalidateQueries({ queryKey: meQuery.queryKey }); // the POS screens read them from /me
      toast.success('Đã lưu cài đặt');
    },
    onError: (error) => toast.error(errorText(error)),
  });

  if (!draft || !stored) return <RoutePending />;

  /** Only what changed is sent: the server logs every key it receives. */
  function saveChanges() {
    const keys = Object.keys(draft!) as (keyof PosSettings)[];
    const changed = Object.fromEntries(keys.filter((key) => draft![key] !== stored![key]).map((key) => [key, draft![key]]));
    if (Object.keys(changed).length === 0) toast('Không có gì thay đổi');
    else save.mutate(changed);
  }
  const set = <K extends keyof PosSettings>(key: K, value: PosSettings[K]) => setDraft({ ...draft, [key]: value });

  return (
    <div className="max-w-3xl">
      <h1 className="text-lg font-semibold">Cài đặt</h1>
      <Row title="Chặn chụp khi máy nghiêng" help="Bật: không cho bấm chụp khi cảm biến báo nghiêng quá 15°. Tắt: chỉ cảnh báo.">
        <Switch label="Chặn chụp khi máy nghiêng" checked={draft.tilt_block_capture} onChange={(on) => set('tilt_block_capture', on)} />
      </Row>
      <Row title="Tự động in hoá đơn" help="Mở hộp thoại in ngay sau khi thanh toán xong.">
        <Switch label="Tự động in hoá đơn" checked={draft.auto_print_receipt} onChange={(on) => set('auto_print_receipt', on)} />
      </Row>
      <Row title="Cho phép thanh toán khi thiếu giá" help="Tắt (khuyến nghị): bắt buộc nhập giá tay trước khi thanh toán.">
        <Switch
          label="Cho phép thanh toán khi thiếu giá"
          checked={draft.allow_checkout_without_price}
          onChange={(on) => set('allow_checkout_without_price', on)}
        />
      </Row>
      <Row
        title="Ngưỡng nhận diện (similarity)"
        help="Cao hơn = nhiều dòng viền vàng hơn, ít nhận sai hơn. Áp dụng ngay ở lần chụp kế tiếp."
      >
        <Threshold
          label="Ngưỡng nhận diện"
          value={draft.similarity_threshold}
          onChange={(value) => set('similarity_threshold', value)}
          min={0.3}
          max={0.95}
          start={0.6}
        />
      </Row>
      <Row
        title="Độ tin cậy tối thiểu để tự chấp nhận"
        help="Cao hơn = nhiều dòng viền vàng hơn, ít chấp nhận nhầm hơn. Áp dụng ngay ở lần chụp kế tiếp."
      >
        <Threshold
          label="Độ tin cậy tối thiểu"
          value={draft.min_confidence_accept}
          onChange={(value) => set('min_confidence_accept', value)}
          min={0.3}
          max={0.99}
          start={0.5}
        />
      </Row>
      <Button className="mt-4" disabled={save.isPending} onClick={saveChanges}>
        Lưu cài đặt
      </Button>
    </div>
  );
}
