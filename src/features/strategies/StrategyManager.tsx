import React, { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useStrategyStore } from '@/stores/strategyStore';
import { Strategy } from '@/types';
import { 
  Play, 
  Square, 
  Trash2, 
  Plus, 
  Activity,
  AlertTriangle,
  CheckCircle
} from 'lucide-react';

export function StrategyManager() {
  const { 
    strategies, 
    loadStrategies, 
    removeStrategy, 
    isLoading 
  } = useStrategyStore();

  useEffect(() => {
    loadStrategies();
  }, [loadStrategies]);

  const strategiesList = Array.from(strategies.values());

  const getStatusIcon = (status: Strategy['status']) => {
    switch (status) {
      case 'RUNNING':
        return <CheckCircle className="h-4 w-4 text-green-500" />;
      case 'STOPPED':
        return <Square className="h-4 w-4 text-gray-500" />;
      case 'ERROR':
        return <AlertTriangle className="h-4 w-4 text-red-500" />;
      default:
        return <Activity className="h-4 w-4 text-gray-500" />;
    }
  };

  const getStatusColor = (status: Strategy['status']) => {
    switch (status) {
      case 'RUNNING':
        return 'text-green-600';
      case 'STOPPED':
        return 'text-gray-600';
      case 'ERROR':
        return 'text-red-600';
      default:
        return 'text-gray-600';
    }
  };

  const handleRemoveStrategy = async (strategyId: string) => {
    if (confirm('Are you sure you want to remove this strategy?')) {
      try {
        await removeStrategy(strategyId);
      } catch (error) {
        console.error('Failed to remove strategy:', error);
      }
    }
  };

  const formatTime = (timestamp: string) => {
    return new Date(timestamp).toLocaleString();
  };

  return (
    <Card className="w-full">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Strategy Manager</CardTitle>
          <Button className="gap-2">
            <Plus className="h-4 w-4" />
            Add Strategy
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Strategy ID</TableHead>
                <TableHead>Contract</TableHead>
                <TableHead>Timeframe</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {strategiesList.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                    {isLoading ? 'Loading strategies...' : 'No strategies configured'}
                  </TableCell>
                </TableRow>
              ) : (
                strategiesList.map((strategy) => (
                  <TableRow key={strategy.id}>
                    <TableCell className="font-medium">
                      {strategy.id}
                    </TableCell>
                    <TableCell className="font-mono">
                      {strategy.contract_id}
                    </TableCell>
                    <TableCell className="font-mono">
                      {strategy.timeframe}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {getStatusIcon(strategy.status)}
                        <span className={`text-sm font-medium ${getStatusColor(strategy.status)}`}>
                          {strategy.status}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatTime(strategy.created_at)}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {strategy.status === 'RUNNING' ? (
                          <Button
                            variant="outline"
                            size="sm"
                            className="gap-1"
                            disabled={isLoading}
                          >
                            <Square className="h-3 w-3" />
                            Stop
                          </Button>
                        ) : (
                          <Button
                            variant="outline"
                            size="sm"
                            className="gap-1"
                            disabled={isLoading}
                          >
                            <Play className="h-3 w-3" />
                            Start
                          </Button>
                        )}
                        
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleRemoveStrategy(strategy.id)}
                          disabled={isLoading}
                          className="text-red-600 hover:text-red-700"
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
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