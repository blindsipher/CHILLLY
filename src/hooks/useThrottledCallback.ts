import { useCallback, useRef } from 'react';

export function useThrottledCallback<T extends any[]>(
  callback: (...args: T) => void,
  delay: number
) {
  const lastCall = useRef(0);
  const timeoutRef = useRef<NodeJS.Timeout>();

  return useCallback((...args: T) => {
    const now = Date.now();
    
    if (now - lastCall.current >= delay) {
      lastCall.current = now;
      callback(...args);
    } else {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => {
        lastCall.current = Date.now();
        callback(...args);
      }, delay - (now - lastCall.current));
    }
  }, [callback, delay]);
}