import React, { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useOrderStore } from '@/stores/orderStore';
import { useMarketStore } from '@/stores/marketStore';
import { useAuthStore } from '@/stores/authStore';
import { OrderType, OrderSide, TimeInForce } from '@/types';
import { TrendingUp, TrendingDown, AlertCircle } from 'lucide-react';

const orderSchema = z.object({
  contract_id: z.string().min(1, 'Contract is required'),
  side: z.enum(['BUY', 'SELL']),
  type: z.enum(['MARKET', 'LIMIT', 'STOP', 'TRAILING_STOP']),
  size: z.number().min(1, 'Size must be at least 1'),
  limit_price: z.number().optional(),
  stop_price: z.number().optional(),
  trail_price: z.number().optional(),
  time_in_force: z.enum(['DAY', 'GTC', 'IOC', 'FOK']).optional(),
});

type OrderFormData = z.infer<typeof orderSchema>;

export function OrderTicket() {
  const { placeOrder, isLoading, error, clearError } = useOrderStore();
  const { contracts, quotes, loadContracts } = useMarketStore();
  
  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
    reset,
  } = useForm<OrderFormData>({
    resolver: zodResolver(orderSchema),
    defaultValues: {
      side: 'BUY',
      type: 'MARKET',
      size: 1,
      time_in_force: 'DAY',
    },
  });

  const orderType = watch('type');
  const selectedContract = watch('contract_id');
  const side = watch('side');

  // Load contracts on mount
  useEffect(() => {
    loadContracts();
  }, [loadContracts]);

  // Clear errors when form changes
  useEffect(() => {
    if (error) {
      clearError();
    }
  }, [watch(), clearError]);

  const onSubmit = async (data: OrderFormData) => {
    try {
      await placeOrder({
        ...data,
        strategy_id: 'manual',
        account_id: '1', // This should come from auth context
      });
      reset();
    } catch (error) {
      // Error is handled by the store
    }
  };

  const currentQuote = selectedContract ? quotes.get(selectedContract) : null;

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {side === 'BUY' ? (
            <TrendingUp className="h-5 w-5 text-green-500" />
          ) : (
            <TrendingDown className="h-5 w-5 text-red-500" />
          )}
          Order Ticket
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div>
            <Select 
              value={selectedContract || ''} 
              onValueChange={(value) => setValue('contract_id', value)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select Contract" />
              </SelectTrigger>
              <SelectContent>
                {contracts.map((contract) => (
                  <SelectItem key={contract.id} value={contract.id}>
                    <div className="flex items-center justify-between w-full">
                      <span>{contract.symbol}</span>
                      <span className="text-muted-foreground text-xs">
                        {contract.name}
                      </span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errors.contract_id && (
              <p className="mt-1 text-sm text-red-500">{errors.contract_id.message}</p>
            )}
          </div>

          {currentQuote && (
            <div className="rounded-md bg-muted/50 p-3 text-sm">
              <div className="flex justify-between">
                <span>Bid: <span className="font-mono">{currentQuote.best_bid}</span></span>
                <span>Ask: <span className="font-mono">{currentQuote.best_ask}</span></span>
              </div>
              <div className="mt-1 text-center">
                <span className={`font-medium ${
                  currentQuote.change >= 0 ? 'text-green-600' : 'text-red-600'
                }`}>
                  {currentQuote.last_price} ({currentQuote.change >= 0 ? '+' : ''}{currentQuote.change_percent.toFixed(2)}%)
                </span>
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-2">
            <Select 
              value={side} 
              onValueChange={(value) => setValue('side', value as OrderSide)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="BUY">
                  <div className="flex items-center gap-2">
                    <TrendingUp className="h-4 w-4 text-green-500" />
                    Buy
                  </div>
                </SelectItem>
                <SelectItem value="SELL">
                  <div className="flex items-center gap-2">
                    <TrendingDown className="h-4 w-4 text-red-500" />
                    Sell
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>

            <Select 
              value={orderType} 
              onValueChange={(value) => setValue('type', value as OrderType)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="MARKET">Market</SelectItem>
                <SelectItem value="LIMIT">Limit</SelectItem>
                <SelectItem value="STOP">Stop</SelectItem>
                <SelectItem value="TRAILING_STOP">Trailing Stop</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div>
            <Input
              type="number"
              placeholder="Quantity"
              {...register('size', { valueAsNumber: true })}
            />
            {errors.size && (
              <p className="mt-1 text-sm text-red-500">{errors.size.message}</p>
            )}
          </div>

          {orderType === 'LIMIT' && (
            <div>
              <Input
                type="number"
                step="0.01"
                placeholder="Limit Price"
                {...register('limit_price', { valueAsNumber: true })}
              />
              {errors.limit_price && (
                <p className="mt-1 text-sm text-red-500">{errors.limit_price.message}</p>
              )}
            </div>
          )}

          {orderType === 'STOP' && (
            <div>
              <Input
                type="number"
                step="0.01"
                placeholder="Stop Price"
                {...register('stop_price', { valueAsNumber: true })}
              />
              {errors.stop_price && (
                <p className="mt-1 text-sm text-red-500">{errors.stop_price.message}</p>
              )}
            </div>
          )}

          {orderType === 'TRAILING_STOP' && (
            <div>
              <Input
                type="number"
                step="0.01"
                placeholder="Trail Amount"
                {...register('trail_price', { valueAsNumber: true })}
              />
              {errors.trail_price && (
                <p className="mt-1 text-sm text-red-500">{errors.trail_price.message}</p>
              )}
            </div>
          )}

          <div>
            <Select 
              defaultValue="DAY"
              onValueChange={(value) => setValue('time_in_force', value as TimeInForce)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="DAY">Day</SelectItem>
                <SelectItem value="GTC">Good Till Canceled</SelectItem>
                <SelectItem value="IOC">Immediate or Cancel</SelectItem>
                <SelectItem value="FOK">Fill or Kill</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {error && (
            <div className="flex items-center gap-2 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/20 dark:text-red-400">
              <AlertCircle className="h-4 w-4" />
              {error}
            </div>
          )}

          <Button 
            type="submit" 
            className={`w-full ${side === 'BUY' ? 'bg-green-600 hover:bg-green-700' : 'bg-red-600 hover:bg-red-700'}`}
            disabled={isLoading}
          >
            {isLoading ? (
              <>
                <div className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                Placing Order...
              </>
            ) : (
              `${side === 'BUY' ? 'Buy' : 'Sell'} ${orderType}`
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}