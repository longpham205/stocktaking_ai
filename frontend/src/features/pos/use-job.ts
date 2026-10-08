import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { toast } from 'sonner';
import { meQuery } from '@/features/auth/use-auth';
import { getJob } from '@/features/pos/api';
import type { Job } from '@/features/pos/types';
import { errorMessage } from '@/lib/errors';
import { qk } from '@/lib/query-keys';

/** A recognition that takes longer than this is given up (a pipeline reload does not count). */
const JOB_TIMEOUT_MS = 120_000;
const POLL_MS = 700;

/** Follows a recognition job until it ends; its result is the order to show. */
export function useJob(jobId: number | undefined, orderId: number) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const started = useRef(Date.now());
  const [slow, setSlow] = useState(false);
  const job = useQuery({
    queryKey: qk.job(jobId ?? 0),
    queryFn: () => getJob(jobId!),
    enabled: jobId !== undefined,
    refetchInterval: (query) => (query.state.data && ['done', 'error'].includes(query.state.data.status) ? false : POLL_MS),
    staleTime: Infinity,
  });
  const data: Job | undefined = job.data;

  useEffect(() => {
    // the counter screen follows one job after another without leaving the page
    started.current = Date.now();
    setSlow(false);
  }, [jobId]);

  useEffect(() => {
    if (!data) return;
    if (data.status === 'done' && data.order) {
      queryClient.setQueryData(qk.order(orderId), data.order);
      void queryClient.invalidateQueries({ queryKey: meQuery.queryKey }); // an admin may have changed the settings
      if (!data.added) {
        const unrecognised = data.warnings?.find((w) => w.type === 'unrecognized_objects');
        toast(
          unrecognised && unrecognised.type === 'unrecognized_objects'
            ? `Phát hiện ${unrecognised.count} vật nhưng chưa nhận diện được — hãy chụp gần hơn hoặc thêm thủ công`
            : 'Không thấy sản phẩm nào — hãy chụp lại hoặc thêm thủ công',
        );
      }
      return;
    }
    if (data.status === 'error') {
      toast.error(errorMessage(data.error?.code ?? 'PIPELINE_ERROR', data.error?.message));
      void navigate({ to: '/pos/orders/$orderId', params: { orderId: String(orderId) }, search: {}, replace: true });
      return;
    }
    if (data.system_reloading) started.current = Date.now();
    const waited = Date.now() - started.current;
    if (waited > 3000) setSlow(true);
    if (waited > JOB_TIMEOUT_MS) {
      toast.error('Quá thời gian chờ, hãy chụp lại');
      void navigate({ to: '/pos/orders/$orderId/capture', params: { orderId: String(orderId) }, replace: true });
    }
  }, [data, orderId, navigate, queryClient]);

  return { job: data, waiting: jobId !== undefined && (!data || !['done', 'error'].includes(data.status)), slow };
}
