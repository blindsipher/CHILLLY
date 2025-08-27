import React, { useEffect } from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useAccountStore } from '@/stores/accountStore';
import { useMarketStore } from '@/stores/marketStore';
import { Position } from '@/types';
import { TrendingUp, TrendingDown } from 'lucide-react';

export function PositionsTable() {
  const { positions, loadPositions } = useAccountStore();
  const { quotes } = useMarketStore();

  useEffect(() => {
    loadPositions();
  }, [loadPositions]);

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
    }).format(value);
  };

  const calculateUnrealizedPnL = (position: Position) => {
    const quote = quotes.get(position.contract_id);
    if (!quote) return position.unrealized_pnl || 0;

    const currentPrice = position.side === 'LONG' ? quote.best_bid : quote.best_ask;
    const priceDiff = currentPrice - position.avg_price;
    
    return position.side === 'LONG' 
      ? priceDiff * position.size
      : -priceDiff * position.size;
  };

  const totalUnrealizedPnL = positions.reduce((total, position) => {
    return total + calculateUnrealizedPnL(position);
  }, 0);

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Positions</span>
          {positions.length > 0 && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Total P&L:</span>
              <span className={`font-medium ${
                totalUnrealizedPnL >= 0 ? 'text-green-600' : 'text-red-600'
              }`}>
                {formatCurrency(totalUnrealizedPnL)}
              </span>
            </div>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Contract</TableHead>
                <TableHead>Side</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>Avg Price</TableHead>
                <TableHead>Current Price</TableHead>
                <TableHead>Unrealized P&L</TableHead>
                <TableHead>% Change</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {positions.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground py-8">
                    No open positions
                  </TableCell>
                </TableRow>
              ) : (
                positions.map((position) => {
                  const quote = quotes.get(position.contract_id);
                  const currentPrice = quote 
                    ? (position.side === 'LONG' ? quote.best_bid : quote.best_ask)
                    : position.avg_price;
                  const unrealizedPnL = calculateUnrealizedPnL(position);
                  const percentChange = ((currentPrice - position.avg_price) / position.avg_price) * 100;
                  const adjustedPercentChange = position.side === 'LONG' ? percentChange : -percentChange;

                  return (
                    <TableRow key={position.id}>
                      <TableCell className="font-medium">
                        {position.contract_id}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          {position.side === 'LONG' ? (
                            <TrendingUp className="h-4 w-4 text-green-500" />
                          ) : (
                            <TrendingDown className="h-4 w-4 text-red-500" />
                          )}
                          <span className={`font-medium ${
                            position.side === 'LONG' ? 'text-green-600' : 'text-red-600'
                          }`}>
                            {position.side}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="font-mono">
                        {position.size}
                      </TableCell>
                      <TableCell className="font-mono">
                        {position.avg_price.toFixed(2)}
                      </TableCell>
                      <TableCell className="font-mono">
                        {currentPrice.toFixed(2)}
                      </TableCell>
                      <TableCell className={`font-mono font-medium ${
                        unrealizedPnL >= 0 ? 'text-green-600' : 'text-red-600'
                      }`}>
                        {formatCurrency(unrealizedPnL)}
                      </TableCell>
                      <TableCell className={`font-mono ${
                        adjustedPercentChange >= 0 ? 'text-green-600' : 'text-red-600'
                      }`}>
                        {adjustedPercentChange >= 0 ? '+' : ''}{adjustedPercentChange.toFixed(2)}%
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}