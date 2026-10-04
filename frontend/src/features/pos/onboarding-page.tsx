import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { Button } from '@/components/ui/button';
import { meQuery } from '@/features/auth/use-auth';
import { markOnboardingSeen } from '@/features/pos/api';
import type { MeOut } from '@/lib/types';

const STEPS: [string, string, string][] = [
  ['📦', 'Đặt hàng vào khung', 'Xếp các món lên bàn, tránh chồng lên nhau, giữ điện thoại thẳng phía trên.'],
  ['📸', 'Chụp một lần', 'Không cần quét từng món. Bấm chụp, hệ thống nhận diện cả rổ hàng.'],
  ['⚠️', 'Viền vàng = cần xác nhận', 'Chạm vào dòng có viền vàng để xác nhận hoặc chọn lại sản phẩm đúng.'],
  ['💵', 'Thanh toán', 'Kiểm tra hoá đơn, chọn tiền mặt hoặc chuyển khoản, xong.'],
];

/**
 * Four screens, once per account. Marked as seen as soon as it shows (not when the last step is
 * reached): leaving half way does not bring it back.
 */
export function OnboardingPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);

  useEffect(() => {
    queryClient.setQueryData<MeOut>(meQuery.queryKey, (me) =>
      me ? { ...me, user: { ...me.user, has_seen_onboarding: true } } : me,
    );
    markOnboardingSeen().catch(() => undefined); // seen or not, the cashier can work
  }, [queryClient]);

  const [icon, title, text] = STEPS[step];
  const last = step === STEPS.length - 1;
  const finish = () => void navigate({ to: '/pos' });
  return (
    <div className="mx-auto max-w-sm space-y-4 py-10 text-center">
      <div className="text-6xl" aria-hidden="true">
        {icon}
      </div>
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="text-muted-foreground">{text}</p>
      <div className="flex gap-2">
        <Button variant="outline" className="flex-1" onClick={finish}>
          Bỏ qua
        </Button>
        <Button className="flex-1" onClick={() => (last ? finish() : setStep(step + 1))}>
          {last ? 'Bắt đầu' : 'Tiếp'}
        </Button>
      </div>
    </div>
  );
}
