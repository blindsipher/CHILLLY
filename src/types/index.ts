// Core trading types
export interface Order {
  id: string;
  strategy_id: string;
  account_id: string;
  contract_id: string;
  type: OrderType;
  side: OrderSide;
  size: number;
  limit_price?: number;
  stop_price?: number;
  trail_price?: number;
  time_in_force: TimeInForce;
  custom_tag?: string;
  status: OrderStatus;
  filled_size: number;
  avg_fill_price?: number;
  created_at: string;
  updated_at: string;
}

export interface OrderRequest {
  strategy_id: string;
  account_id: string;
  contract_id: string;
  type: OrderType;
  side: OrderSide;
  size: number;
  limit_price?: number;
  stop_price?: number;
  trail_price?: number;
  time_in_force?: TimeInForce;
  custom_tag?: string;
}

export type OrderType = 'MARKET' | 'LIMIT' | 'STOP' | 'TRAILING_STOP' | 'JOIN_BID' | 'JOIN_ASK';
export type OrderSide = 'BUY' | 'SELL';
export type TimeInForce = 'DAY' | 'GTC' | 'IOC' | 'FOK';
export type OrderStatus = 'PENDING' | 'ACCEPTED' | 'WORKING' | 'FILLED' | 'PARTIAL_FILL' | 'CANCELED' | 'REJECTED' | 'EXPIRED';

// Market data types
export interface Contract {
  id: string;
  name: string;
  symbol: string;
  contract_type: string;
  market: string;
  is_active: boolean;
  tick_size: number;
  point_value: number;
  currency: string;
  description?: string;
}

export interface Bar {
  timestamp: string;
  contract_id: string;
  timeframe: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  source: string;
  revision: number;
}

export interface Quote {
  contract_id: string;
  symbol: string;
  last_price: number;
  best_bid: number;
  best_ask: number;
  change: number;
  change_percent: number;
  volume: number;
  timestamp: string;
}

// Account types
export interface Position {
  id: string;
  account_id: string;
  contract_id: string;
  side: 'LONG' | 'SHORT';
  size: number;
  avg_price: number;
  unrealized_pnl: number;
  created_at: string;
}

export interface Account {
  id: string;
  name: string;
  balance: number;
  buying_power: number;
  day_pnl: number;
  total_pnl: number;
  can_trade: boolean;
  is_active: boolean;
}

export interface Trade {
  id: string;
  account_id: string;
  contract_id: string;
  order_id: string;
  side: OrderSide;
  size: number;
  price: number;
  fees: number;
  pnl: number;
  timestamp: string;
}

// Strategy types
export interface Strategy {
  id: string;
  name: string;
  class: string;
  account_id: string;
  contract_id: string;
  timeframe: string;
  params: Record<string, any>;
  status: 'RUNNING' | 'STOPPED' | 'ERROR';
  created_at: string;
  updated_at: string;
}

export interface StrategyConfig {
  strategy_id: string;
  class: string;
  account_id: string;
  contract_id: string;
  timeframe: string;
  params: Record<string, any>;
  risk?: {
    max_position_size?: number;
    max_daily_loss?: number;
    max_order_size?: number;
    max_orders_per_minute?: number;
  };
}

export interface StrategyPerformance {
  strategy_id: string;
  total_pnl: number;
  total_trades: number;
  win_rate: number;
  sharpe_ratio: number;
  max_drawdown: number;
  avg_trade_pnl: number;
}

// System types
export interface HealthStatus {
  status: 'healthy' | 'warning' | 'error' | 'critical';
  components: Record<string, ComponentHealth>;
  timestamp: string;
}

export interface ComponentHealth {
  status: 'healthy' | 'warning' | 'error';
  last_update: string;
  details: Record<string, any>;
}

// WebSocket message types
export interface WebSocketMessage {
  topic: string;
  payload: any;
}

// API response types
export interface StatusResponse {
  status: string;
}

export interface ApiError {
  message: string;
  code?: string;
  details?: any;
}