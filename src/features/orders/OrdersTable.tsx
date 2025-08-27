import React, { useEffect } from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useOrderStore } from '@/stores/orderStore';
import { Order } from '@/types';
import { X, Clock, CheckCircle, AlertCircle, Ban } from 'lucide-react';

export function OrdersTable() {
  const { orders, loadOrders, cancelOrder, isLoading } = useOrderStore();

  useEffect(() => {
    loadOrders();
  }, [loadOrders]);

  const ordersList = Array.from(orders.values()).sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );

  const getStatusIcon = (status: Order['status']) => {
    switch (status) {
      case 'PENDING':
        return <Clock className="h-4 w-4 text-yellow-500" />;
      case 'WORKING':
        return <Clock className="h-4 w-4 text-blue-500" />;
      case 'FILLED':
        return <CheckCircle className="h-4 w-4 text-green-500" />;
      case 'PARTIAL_FILL':
        return <AlertCircle className="h-4 w-4 text-orange-500" />;
      case 'CANCELED':
        return <Ban className="h-4 w-4 text-gray-500" />;
      case 'REJECTED':
        return <X className="h-4 w-4 text-red-500" />;
      default:
        return <Clock className="h-4 w-4 text-gray-500" />;
    }
  };

  const getStatusColor = (status: Order['status']) => {
    switch (status) {
      case 'FILLED':
        return 'text-green-600';
      case 'REJECTED':
        return 'text-red-600';
      case 'CANCELED':
        return 'text-gray-600';
      case 'PARTIAL_FILL':
        return 'text-orange-600';
      case 'WORKING':
        return 'text-blue-600';
      default:
        return 'text-yellow-600';
    }
  };

  const formatTime = (timestamp: string) => {
    return new Date(timestamp).toLocaleTimeString();
  };

  const formatPrice = (price: number | undefined) => {
    return price ? price.toFixed(2) : '-';
  };

  const canCancel = (order: Order) => {
    return order.status === 'WORKING' || order.status === 'PENDING';
  };

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle>Orders</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Contract</TableHead>
                <TableHead>Side</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>Price</TableHead>
                <TableHead>Filled</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ordersList.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={9} className="text-center text-muted-foreground py-8">
                    {isLoading ? 'Loading orders...' : 'No orders found'}
                  </TableCell>
                </TableRow>
              ) : (
                ordersList.map((order) => (
                  <TableRow key={order.id}>
                    <TableCell className="font-mono text-sm">
                      {formatTime(order.created_at)}
                    </TableCell>
                    <TableCell className="font-medium">
                      {order.contract_id}
                    </TableCell>
                    <TableCell>
                      <span className={`font-medium ${
                        order.side === 'BUY' ? 'text-green-600' : 'text-red-600'
                      }`}>
                        {order.side}
                      </span>
                    </TableCell>
                    <TableCell>{order.type}</TableCell>
                    <TableCell className="font-mono">
                      {order.size}
                    </TableCell>
                    <TableCell className="font-mono">
                      {order.type === 'MARKET' ? 'MKT' : formatPrice(order.limit_price || order.stop_price)}
                    </TableCell>
                    <TableCell className="font-mono">
                      {order.filled_size}/{order.size}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {getStatusIcon(order.status)}
                        <span className={`text-sm font-medium ${getStatusColor(order.status)}`}>
                          {order.status.replace('_', ' ')}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      {canCancel(order) && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => cancelOrder(order.id)}
                          disabled={isLoading}
                        >
                          Cancel
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}