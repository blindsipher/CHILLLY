import React, { useEffect } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { TradingHeader } from '@/components/TradingHeader';
import { OrderTicket } from '@/features/orders/OrderTicket';
import { OrdersTable } from '@/features/orders/OrdersTable';
import { PositionsTable } from '@/features/positions/PositionsTable';
import { StrategyManager } from '@/features/strategies/StrategyManager';
import { TradingChart } from '@/features/charts/TradingChart';
import { MarketWatch } from '@/features/market/MarketWatch';
import { SystemHealth } from '@/features/system/SystemHealth';
import { useAccountStore } from '@/stores/accountStore';
import { useMarketStore } from '@/stores/marketStore';

export function TradingDashboard() {
  const { loadAccount, loadPositions, loadTrades } = useAccountStore();
  const { selectedContract } = useMarketStore();

  useEffect(() => {
    // Load initial data
    loadAccount();
    loadPositions();
    loadTrades();
  }, [loadAccount, loadPositions, loadTrades]);

  return (
    <div className="min-h-screen bg-background">
      <TradingHeader />
      
      <div className="container mx-auto p-6">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 h-[calc(100vh-120px)]">
          {/* Left Panel - Order Ticket & Market Watch */}
          <div className="lg:col-span-1 space-y-6">
            <OrderTicket />
            <div className="h-[400px] overflow-hidden">
              <MarketWatch />
            </div>
          </div>

          {/* Center Panel - Chart */}
          <div className="lg:col-span-2">
            <TradingChart 
              contract={selectedContract || 'ES'} 
              height={600}
            />
          </div>

          {/* Right Panel - Tabs */}
          <div className="lg:col-span-1">
            <Tabs defaultValue="orders" className="h-full">
              <TabsList className="grid w-full grid-cols-4">
                <TabsTrigger value="orders">Orders</TabsTrigger>
                <TabsTrigger value="positions">Positions</TabsTrigger>
                <TabsTrigger value="strategies">Strategies</TabsTrigger>
                <TabsTrigger value="system">System</TabsTrigger>
              </TabsList>
              
              <TabsContent value="orders" className="mt-4 h-[calc(100%-60px)] overflow-auto">
                <OrdersTable />
              </TabsContent>
              
              <TabsContent value="positions" className="mt-4 h-[calc(100%-60px)] overflow-auto">
                <PositionsTable />
              </TabsContent>
              
              <TabsContent value="strategies" className="mt-4 h-[calc(100%-60px)] overflow-auto">
                <StrategyManager />
              </TabsContent>
              
              <TabsContent value="system" className="mt-4 h-[calc(100%-60px)] overflow-auto">
                <SystemHealth />
              </TabsContent>
            </Tabs>
          </div>
        </div>
      </div>
    </div>
  );
}