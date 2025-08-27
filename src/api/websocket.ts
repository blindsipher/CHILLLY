import { WebSocketMessage } from '@/types';

export class WebSocketManager {
  private ws: WebSocket | null = null;
  private subscribers = new Map<string, Set<Function>>();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;
  private isConnecting = false;
  private shouldReconnect = true;

  constructor(private url: string) {}

  connect(token: string): Promise<void> {
    if (this.isConnecting || (this.ws && this.ws.readyState === WebSocket.OPEN)) {
      return Promise.resolve();
    }

    this.isConnecting = true;
    this.shouldReconnect = true;

    return new Promise((resolve, reject) => {
      try {
        const wsUrl = `${this.url}/${encodeURIComponent(token)}`;
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
          console.log('WebSocket connected');
          this.reconnectAttempts = 0;
          this.isConnecting = false;
          resolve();
        };

        this.ws.onmessage = (event) => {
          try {
            const message: WebSocketMessage = JSON.parse(event.data);
            this.handleMessage(message);
          } catch (error) {
            console.error('Failed to parse WebSocket message:', error);
          }
        };

        this.ws.onclose = (event) => {
          console.log('WebSocket disconnected:', event.code, event.reason);
          this.isConnecting = false;
          
          if (this.shouldReconnect) {
            this.handleReconnect(token);
          }
        };

        this.ws.onerror = (error) => {
          console.error('WebSocket error:', error);
          this.isConnecting = false;
          reject(error);
        };
      } catch (error) {
        this.isConnecting = false;
        reject(error);
      }
    });
  }

  private handleMessage(message: WebSocketMessage) {
    const { topic, payload } = message;
    
    // Notify exact topic subscribers
    const exactSubscribers = this.subscribers.get(topic);
    if (exactSubscribers) {
      exactSubscribers.forEach(callback => {
        try {
          callback(payload);
        } catch (error) {
          console.error('Error in WebSocket callback:', error);
        }
      });
    }

    // Notify wildcard subscribers
    this.subscribers.forEach((callbacks, pattern) => {
      if (pattern.includes('*') && this.matchesPattern(topic, pattern)) {
        callbacks.forEach(callback => {
          try {
            callback(payload);
          } catch (error) {
            console.error('Error in WebSocket wildcard callback:', error);
          }
        });
      }
    });
  }

  private matchesPattern(topic: string, pattern: string): boolean {
    const regex = new RegExp(pattern.replace(/\*/g, '.*'));
    return regex.test(topic);
  }

  private async handleReconnect(token: string) {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max reconnection attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
    
    console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
    
    setTimeout(() => {
      if (this.shouldReconnect) {
        this.connect(token).catch(error => {
          console.error('Reconnection failed:', error);
        });
      }
    }, delay);
  }

  subscribe(topic: string, callback: Function): () => void {
    if (!this.subscribers.has(topic)) {
      this.subscribers.set(topic, new Set());
    }
    
    this.subscribers.get(topic)!.add(callback);

    // Return unsubscribe function
    return () => {
      const subscribers = this.subscribers.get(topic);
      if (subscribers) {
        subscribers.delete(callback);
        if (subscribers.size === 0) {
          this.subscribers.delete(topic);
        }
      }
    };
  }

  disconnect() {
    this.shouldReconnect = false;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.subscribers.clear();
    this.reconnectAttempts = 0;
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  get connectionState(): string {
    if (!this.ws) return 'disconnected';
    
    switch (this.ws.readyState) {
      case WebSocket.CONNECTING:
        return 'connecting';
      case WebSocket.OPEN:
        return 'connected';
      case WebSocket.CLOSING:
        return 'closing';
      case WebSocket.CLOSED:
        return 'disconnected';
      default:
        return 'unknown';
    }
  }
}

// Create singleton instance
export const wsManager = new WebSocketManager(import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws');