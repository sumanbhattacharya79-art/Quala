import { useEffect, useState } from "react";

export const MOBILE_MAX_WIDTH_PX = 768;

export function readIsMobileViewport() {
  if (typeof window === "undefined") return false;
  return window.matchMedia(`(max-width: ${MOBILE_MAX_WIDTH_PX}px)`).matches;
}

/** True when viewport width ≤ MOBILE_MAX_WIDTH_PX (768px). */
export function useMobileViewport() {
  const [isMobile, setIsMobile] = useState(readIsMobileViewport);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${MOBILE_MAX_WIDTH_PX}px)`);
    const onChange = () => setIsMobile(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return isMobile;
}
