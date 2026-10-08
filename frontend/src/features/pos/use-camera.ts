import { useCallback, useEffect, useRef, useState } from 'react';
import { TILT_LIMIT, tiltAngle } from '@/features/pos/lib';

export type CameraFailure = 'insecure' | 'unsupported' | 'denied' | 'error';

const PHONE_TAIL = ' Vẫn dùng bình thường: bấm "Chụp bằng camera máy" hoặc "Chọn ảnh".';

export function cameraFailureText(why: CameraFailure, tail = PHONE_TAIL): string {
  if (why === 'insecure') {
    return 'Trang đang mở bằng địa chỉ http:// nên trình duyệt không cho xem trước camera (chỉ cho phép với https://).' + tail;
  }
  if (why === 'denied') {
    return 'Trình duyệt chưa được cấp quyền camera cho trang này — cho phép trong cài đặt trình duyệt rồi tải lại trang.' + tail;
  }
  if (why === 'unsupported') return 'Trình duyệt này không hỗ trợ xem trước camera.' + tail;
  return 'Camera đang bận hoặc không có trên máy này.' + tail;
}

/** A camera the browser can see (a webcam, or a phone connected as one). */
export interface VideoInput {
  deviceId: string;
  label: string;
}

/**
 * The live preview of `deviceId` (none: the back camera) on `videoRef`; stopped when the screen
 * goes away. `devices` lists the cameras once the browser has granted access (labels need it).
 */
export function useCamera(deviceId?: string) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [failure, setFailure] = useState<CameraFailure | null>(null);
  const [ready, setReady] = useState(false);
  const [devices, setDevices] = useState<VideoInput[]>([]);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let cancelled = false;
    setFailure(null);
    setReady(false);
    if (window.isSecureContext === false) {
      setFailure('insecure'); // http://<LAN IP>: the browser blocks getUserMedia
      return undefined;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setFailure('unsupported');
      return undefined;
    }
    navigator.mediaDevices
      .getUserMedia({
        video: {
          ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } }),
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      })
      .then(async (media) => {
        if (cancelled) {
          media.getTracks().forEach((track) => track.stop());
          return;
        }
        stream = media;
        const video = videoRef.current;
        if (video) {
          video.srcObject = media;
          await video.play().catch(() => undefined);
          setReady(true);
        }
      })
      .catch((error: unknown) => {
        const name = error instanceof DOMException ? error.name : '';
        setFailure(name === 'NotAllowedError' || name === 'SecurityError' ? 'denied' : 'error');
      });
    return () => {
      cancelled = true;
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, [deviceId]);

  useEffect(() => {
    const media = navigator.mediaDevices;
    if (!ready || !media?.enumerateDevices) return undefined;
    const list = () => {
      media
        .enumerateDevices()
        .then((all) =>
          setDevices(
            all
              .filter((device) => device.kind === 'videoinput')
              .map((device, index) => ({ deviceId: device.deviceId, label: device.label || `Camera ${index + 1}` })),
          ),
        )
        .catch(() => undefined); // no list: the camera on screen still works
    };
    list();
    media.addEventListener?.('devicechange', list); // a webcam plugged in or out
    return () => media.removeEventListener?.('devicechange', list);
  }, [ready]);

  /** The current frame as a JPEG, or null while the camera is not playing. */
  const grab = useCallback(async (): Promise<Blob | null> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return null;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d')?.drawImage(video, 0, 0);
    return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
  }, []);

  return { videoRef, failure, ready, grab, devices };
}

/** How tilted the phone is, from its orientation sensor (null without one, or before permission). */
export function useTilt() {
  const [angle, setAngle] = useState<number | null>(null);
  const [needsPermission, setNeedsPermission] = useState(false);
  const [listening, setListening] = useState(false);

  useEffect(() => {
    const Orientation = window.DeviceOrientationEvent as
      | (typeof DeviceOrientationEvent & { requestPermission?: () => Promise<string> })
      | undefined;
    if (!Orientation) return undefined;
    if (typeof Orientation.requestPermission === 'function' && !listening) {
      setNeedsPermission(true); // iOS: only after a tap
      return undefined;
    }
    let last = 0;
    const onOrientation = (event: DeviceOrientationEvent) => {
      if (Date.now() - last < 500) return;
      last = Date.now();
      setAngle(tiltAngle(event.beta, event.gamma));
    };
    window.addEventListener('deviceorientation', onOrientation);
    return () => window.removeEventListener('deviceorientation', onOrientation);
  }, [listening]);

  const requestPermission = useCallback(async () => {
    const Orientation = window.DeviceOrientationEvent as unknown as { requestPermission?: () => Promise<string> };
    try {
      if ((await Orientation.requestPermission?.()) === 'granted') {
        setNeedsPermission(false);
        setListening(true);
      }
    } catch {
      // refused: the photo is still taken, only without the tilt check
    }
  }, []);

  return { angle, tooTilted: angle !== null && angle > TILT_LIMIT, needsPermission, requestPermission };
}
