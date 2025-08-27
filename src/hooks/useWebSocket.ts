import { useEffect, useRef } from 'react';
import { wsManager } from '@/api/websocket';

export function useWebSocket<T = any>(
  topic: string,
  callback: (data: T) => void,
  deps: any[] = []
) {
  const callbackRef = useRef(callback);
  
  // Update callback ref when callback changes
  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => {
    const unsubscribe = wsManager.subscribe(topic, (data: T) => {
      callbackRef.current(data);
    });

    return unsubscribe;
  }, [topic, ...deps]);
}