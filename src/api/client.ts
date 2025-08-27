import { 
  Order, 
  OrderRequest, 
  Strategy, 
  StrategyConfig, 
  StrategyPerformance,
  Contract, 
  Bar, 
  Quote, 
  Position, 
  Account, 
  Trade, 
  HealthStatus,
  StatusResponse 
} from '@/types';

export class ChillllyAPI {
  private baseURL: string;
  private token: string | null = null;

  constructor(baseURL: string) {
    this.baseURL = baseURL;
  }

  setToken(token: string) {
    this.token = token;
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.baseURL}${endpoint}`;
    const headers = {
      'Content-Type': 'application/json',
      ...(this.token && { Authorization: `Bearer ${this.token}` }),
      ...options.headers,
    };

    const response = await fetch(url, { ...options, headers });
    
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`API Error: ${response.status} ${response.statusText} - ${errorText}`);
    }

    return response.json();
  }

  // Authentication
  async authenticate(username: string, apiKey: string): Promise<{ token: string }> {
    return this.request('/auth/token', {
      method: 'POST',
      body: JSON.stringify({ username, api_key: apiKey }),
    });
  }

  // Order Management
  async placeOrder(order: OrderRequest): Promise<StatusResponse> {
    return this.request('/orders', {
      method: 'POST',
      body: JSON.stringify(order),
    });
  }

  async getOrders(): Promise<Record<string, Order>> {
    return this.request('/orders');
  }

  async getOrder(orderId: string): Promise<Order> {
    return this.request(`/orders/${orderId}`);
  }

  async cancelOrder(orderId: string): Promise<StatusResponse> {
    return this.request(`/orders/${orderId}/cancel`, {
      method: 'POST',
    });
  }

  // Strategy Management
  async addStrategy(config: StrategyConfig): Promise<StatusResponse> {
    return this.request('/strategies', {
      method: 'POST',
      body: JSON.stringify(config),
    });
  }

  async getStrategies(): Promise<Strategy[]> {
    return this.request('/strategies');
  }

  async getAvailableStrategies(): Promise<any[]> {
    return this.request('/strategies/available');
  }

  async getStrategyPerformance(strategyId: string): Promise<StrategyPerformance> {
    return this.request(`/strategies/${strategyId}/performance`);
  }

  async updateStrategy(strategyId: string, config: any): Promise<StatusResponse> {
    return this.request(`/strategies/${strategyId}`, {
      method: 'PUT',
      body: JSON.stringify(config),
    });
  }

  async removeStrategy(strategyId: string): Promise<StatusResponse> {
    return this.request(`/strategies/${strategyId}`, {
      method: 'DELETE',
    });
  }

  // Market Data
  async getContracts(): Promise<Contract[]> {
    return this.request('/contracts');
  }

  async getMarketData(contract: string): Promise<any> {
    return this.request(`/market-data/${contract}`);
  }

  async getBars(contract: string): Promise<Bar[]> {
    return this.request(`/bars/${contract}`);
  }

  async getQuotes(contract: string): Promise<Quote> {
    return this.request(`/quotes/${contract}`);
  }

  // Account & Trading
  async getPositions(): Promise<Position[]> {
    return this.request('/positions');
  }

  async getAccount(): Promise<Account> {
    return this.request('/account');
  }

  async getTrades(): Promise<Trade[]> {
    return this.request('/trades');
  }

  // System Monitoring
  async getHealth(): Promise<HealthStatus> {
    return this.request('/health');
  }

  async getMetrics(): Promise<any> {
    return this.request('/metrics');
  }

  async getLogs(): Promise<string[]> {
    return this.request('/logs');
  }

  async getConfig(): Promise<any> {
    return this.request('/config');
  }

  async getStatus(): Promise<any> {
    return this.request('/status');
  }
}

// Create singleton instance
export const api = new ChillllyAPI(import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000');