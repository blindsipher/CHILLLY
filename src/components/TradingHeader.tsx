import React from 'react';
import { Button } from '@/components/ui/button';
import { useAuthStore } from '@/stores/authStore';
import { useAccountStore } from '@/stores/accountStore';
import { wsManager } from '@/api/websocket';
import { 
  LogOut, 
  Activity, 
  Wifi, 
  WifiOff, 
  DollarSign,
  TrendingUp,
  TrendingDown
} from 'lucide-react';

export function TradingHeader() {
  const { username, logout } = useAuthStore();
  const { account } = useAccountStore();
  const isConnected = wsManager.isConnected;

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
    }).format(value);
  };

  const formatPnL = (value: number) => {
    const formatted = formatCurrency(Math.abs(value));
    return value >= 0 ? `+${formatted}` : `-${formatted}`;
  };

  return (
    <header className="flex items-center justify-between border-b bg-card px-6 py-3">
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2">
          <Activity className="h-6 w-6 text-primary" />
          <h1 className="text-xl font-bold">CHILLLY</h1>
        </div>

        <div className="flex items-center gap-2 text-sm">
          {isConnected ? (
            <>
              <Wifi className="h-4 w-4 text-green-500" />
              <span className="text-green-600">Connected</span>
            </>
          ) : (
            <>
              <WifiOff className="h-4 w-4 text-red-500" />
              <span className="text-red-600">Disconnected</span>
            </>
          )}
        </div>
      </div>

      <div className="flex items-center gap-6">
        {account && (
          <div className="flex items-center gap-4 text-sm">
            <div className="flex items-center gap-1">
              <DollarSign className="h-4 w-4 text-muted-foreground" />
              <span className="font-medium">
                {formatCurrency(account.balance)}
              </span>
            </div>
            
            <div className="flex items-center gap-1">
              {account.day_pnl >= 0 ? (
                <TrendingUp className="h-4 w-4 text-green-500" />
              ) : (
                <TrendingDown className="h-4 w-4 text-red-500" />
              )}
              <span className={`font-medium ${
                account.day_pnl >= 0 ? 'text-green-600' : 'text-red-600'
              }`}>
                {formatPnL(account.day_pnl)}
              </span>
            </div>
          </div>
        )}

        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">
            Welcome, {username}
          </span>
          
          <Button
            variant="outline"
            size="sm"
            onClick={logout}
            className="gap-2"
          >
            <LogOut className="h-4 w-4" />
            Sign Out
          </Button>
        </div>
      </div>
    </header>
  );
}