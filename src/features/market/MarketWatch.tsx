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
import { useMarketStore } from '@/stores/marketStore';
import { Contract, Quote } from '@/types';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';

export function MarketWatch() {
  const { contracts, quotes, loadContracts, setSelectedContract } = useMarketStore();

  useEffect(() => {
    loadContracts();
  }, [loadContracts]);

  const formatPrice = (price: number) => {
    return price.toFixed(2);
  };

  const formatChange = (change: number) => {
    return change >= 0 ? `+${change.toFixed(2)}` : change.toFixed(2);
  };

  const formatPercent = (percent: number) => {
    return `${percent >= 0 ? '+' : ''}${percent.toFixed(2)}%`;
  };

  const getChangeIcon = (change: number) => {
    if (change > 0) return <TrendingUp className="h-4 w-4 text-green-500" />;
    if (change < 0) return <TrendingDown className="h-4 w-4 text-red-500" />;
    return <Minus className="h-4 w-4 text-gray-500" />;
  };

  const getChangeColor = (change: number) => {
    if (change > 0) return 'text-green-600';
    if (change < 0) return 'text-red-600';
    return 'text-gray-600';
  };

  const handleRowClick = (contract: Contract) => {
    setSelectedContract(contract.id);
  };

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle>Market Watch</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Last</TableHead>
                <TableHead>Change</TableHead>
                <TableHead>%</TableHead>
                <TableHead>Bid</TableHead>
                <TableHead>Ask</TableHead>
                <TableHead>Volume</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {contracts.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground py-8">
                    Loading contracts...
                  </TableCell>
                </TableRow>
              ) : (
                contracts
                  .filter(contract => contract.is_active)
                  .slice(0, 20) // Show top 20 contracts
                  .map((contract) => {
                    const quote = quotes.get(contract.id);
                    
                    return (
                      <TableRow 
                        key={contract.id}
                        className="cursor-pointer hover:bg-muted/50"
                        onClick={() => handleRowClick(contract)}
                      >
                        <TableCell className="font-medium">
                          <div>
                            <div className="font-semibold">{contract.symbol}</div>
                            <div className="text-xs text-muted-foreground">
                              {contract.name}
                            </div>
                          </div>
                        </TableCell>
                        <TableCell className="font-mono">
                          {quote ? formatPrice(quote.last_price) : '-'}
                        </TableCell>
                        <TableCell>
                          {quote ? (
                            <div className="flex items-center gap-1">
                              {getChangeIcon(quote.change)}
                              <span className={`font-mono ${getChangeColor(quote.change)}`}>
                                {formatChange(quote.change)}
                              </span>
                            </div>
                          ) : (
                            '-'
                          )}
                        </TableCell>
                        <TableCell>
                          {quote ? (
                            <span className={`font-mono ${getChangeColor(quote.change_percent)}`}>
                              {formatPercent(quote.change_percent)}
                            </span>
                          ) : (
                            '-'
                          )}
                        </TableCell>
                        <TableCell className="font-mono">
                          {quote ? formatPrice(quote.best_bid) : '-'}
                        </TableCell>
                        <TableCell className="font-mono">
                          {quote ? formatPrice(quote.best_ask) : '-'}
                        </TableCell>
                        <TableCell className="font-mono">
                          {quote ? quote.volume.toLocaleString() : '-'}
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