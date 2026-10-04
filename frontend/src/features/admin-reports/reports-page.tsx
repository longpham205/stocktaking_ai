import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { RoutePending } from '@/components/route-states';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api-client';
import { formatVnd } from '@/lib/format';
import { qk } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

export type ReportRange = 'today' | '7d' | '30d';

/** Mirrors backend reports/schemas.py `ReportOut`. */
export interface Report {
  range: ReportRange;
  range_orders: number;
  range_revenue: number;
  daily: { date: string; orders: number; revenue: number }[];
  top_products: { product_id: string; name: string; quantity: number; revenue: number }[];
  orders_today: number;
  revenue_today: number;
  captures_today: number;
  error_rate: number;
  avg_processing_ms: number | null;
  active_shifts: number;
  active_staff: number;
  queue_size: number;
  products_total: number;
  products_missing_price: number;
  products_missing_barcode: number;
}

const RANGES: [ReportRange, string][] = [
  ['today', 'Hôm nay'],
  ['7d', '7 ngày'],
  ['30d', '30 ngày'],
];
/** The dashboard is watched during the day: it refreshes by itself. */
const REFRESH_MS = 15_000;

function Kpi({ label, value, warn }: { label: string; value: string | number; warn?: boolean }) {
  return (
    <Card className="p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn('text-xl font-bold', warn && 'text-destructive')} data-warn={warn || undefined}>
        {value}
      </div>
    </Card>
  );
}

/** YYYY-MM-DD -> DD/MM */
const shortDate = (date: string) => `${date.slice(8)}/${date.slice(5, 7)}`;

function DailyBars({ daily }: { daily: Report['daily'] }) {
  const max = Math.max(1, ...daily.map((day) => day.revenue));
  return (
    <Card className="p-4">
      <div className="flex h-40 items-end gap-1" role="img" aria-label="Doanh thu theo ngày">
        {daily.map((day, index) => (
          <div
            key={day.date}
            className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1"
            title={`${day.date}: ${formatVnd(day.revenue)} · ${day.orders} đơn`}
            data-testid="day-bar"
          >
            <div className="w-full rounded-t bg-primary/80" style={{ height: `${Math.max(1, Math.round((day.revenue / max) * 100))}%` }} />
            <span className="h-4 text-[10px] text-muted-foreground">
              {/* 30 bars: a label every fifth day and on the last one */}
              {daily.length <= 7 || index % 5 === 0 || index === daily.length - 1 ? shortDate(day.date) : ''}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

/** Today's figures, then revenue per day and the best sellers over a range. */
export function ReportsPage() {
  const [range, setRange] = useState<ReportRange>('7d');
  const report = useQuery({
    queryKey: qk.adminReport(range),
    queryFn: () => apiFetch<Report>(`/api/admin/reports?range=${range}`),
    refetchInterval: REFRESH_MS,
  }).data;
  if (!report) return <RoutePending />;

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Báo cáo hôm nay</h1>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <Kpi label="Đơn đã thu" value={report.orders_today} />
        <Kpi label="Doanh thu" value={formatVnd(report.revenue_today)} />
        <Kpi label="Số ảnh xử lý" value={report.captures_today} />
        <Kpi label="Tỉ lệ lỗi" value={`${(report.error_rate * 100).toFixed(1)}%`} warn={report.error_rate > 0.1} />
        <Kpi label="Độ trễ TB" value={report.avg_processing_ms === null ? '—' : `${Math.round(report.avg_processing_ms)} ms`} />
        <Kpi label="Ca đang mở" value={report.active_shifts} />
        <Kpi label="Hàng chờ" value={report.queue_size} />
        <Kpi
          label="SKU thiếu giá"
          value={`${report.products_missing_price}/${report.products_total}`}
          warn={report.products_missing_price > 0}
        />
        <Kpi label="SKU thiếu barcode" value={`${report.products_missing_barcode}/${report.products_total}`} />
      </div>

      <div className="flex items-center justify-between gap-2 pt-2">
        <h2 className="text-lg font-semibold">Doanh thu theo ngày</h2>
        <select
          aria-label="Khoảng báo cáo"
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          value={range}
          onChange={(event) => setRange(event.target.value as ReportRange)}
        >
          {RANGES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:max-w-md">
        <Kpi label="Doanh thu" value={formatVnd(report.range_revenue)} />
        <Kpi label="Số đơn" value={report.range_orders} />
      </div>
      <DailyBars daily={report.daily} />

      <h2 className="text-base font-semibold">Top sản phẩm bán chạy</h2>
      {report.top_products.length === 0 ? (
        <p className="text-sm text-muted-foreground">Chưa có đơn nào trong khoảng này.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th className="py-2 font-medium">Sản phẩm</th>
              <th className="py-2 text-right font-medium">Số lượng</th>
              <th className="py-2 text-right font-medium">Doanh thu</th>
            </tr>
          </thead>
          <tbody>
            {report.top_products.map((product) => (
              <tr key={product.product_id} className="border-b border-border">
                <td className="py-2">{product.name}</td>
                <td className="py-2 text-right">{product.quantity}</td>
                <td className="py-2 text-right">{formatVnd(product.revenue)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
