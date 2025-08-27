import React, { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { api } from '@/api/client';
import { HealthStatus } from '@/types';
import { 
  Activity, 
  CheckCircle, 
  AlertTriangle, 
  XCircle,
  Clock
} from 'lucide-react';

export function SystemHealth() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const loadHealthData = async () => {
      try {
        const [healthData, metricsData] = await Promise.all([
          api.getHealth(),
          api.getMetrics(),
        ]);
        
        setHealth(healthData);
        setMetrics(metricsData);
      } catch (error) {
        console.error('Failed to load health data:', error);
      } finally {
        setIsLoading(false);
      }
    };

    loadHealthData();
    
    // Refresh every 30 seconds
    const interval = setInterval(loadHealthData, 30000);
    return () => clearInterval(interval);
  }, []);

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'healthy':
        return <CheckCircle className="h-4 w-4 text-green-500" />;
      case 'warning':
        return <AlertTriangle className="h-4 w-4 text-yellow-500" />;
      case 'error':
      case 'critical':
        return <XCircle className="h-4 w-4 text-red-500" />;
      default:
        return <Clock className="h-4 w-4 text-gray-500" />;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'healthy':
        return 'text-green-600';
      case 'warning':
        return 'text-yellow-600';
      case 'error':
      case 'critical':
        return 'text-red-600';
      default:
        return 'text-gray-600';
    }
  };

  if (isLoading) {
    return (
      <Card className="w-full">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            System Health
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center text-muted-foreground py-8">
            Loading system health...
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="h-5 w-5" />
          System Health
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {/* Overall Status */}
          {health && (
            <div className="flex items-center justify-between p-4 rounded-lg border">
              <div className="flex items-center gap-3">
                {getStatusIcon(health.status)}
                <div>
                  <div className="font-medium">Overall Status</div>
                  <div className={`text-sm ${getStatusColor(health.status)}`}>
                    {health.status.toUpperCase()}
                  </div>
                </div>
              </div>
              <div className="text-sm text-muted-foreground">
                Last check: {new Date(health.timestamp).toLocaleTimeString()}
              </div>
            </div>
          )}

          {/* Strategy Metrics */}
          {metrics && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="p-4 rounded-lg border bg-card">
                <div className="text-2xl font-bold text-primary">
                  {metrics.total_strategies || 0}
                </div>
                <div className="text-sm text-muted-foreground">
                  Total Strategies
                </div>
              </div>
              
              <div className="p-4 rounded-lg border bg-card">
                <div className="text-2xl font-bold text-green-600">
                  {metrics.running_strategies || 0}
                </div>
                <div className="text-sm text-muted-foreground">
                  Running
                </div>
              </div>
              
              <div className="p-4 rounded-lg border bg-card">
                <div className="text-2xl font-bold text-blue-600">
                  {Object.keys(metrics.strategies || {}).length}
                </div>
                <div className="text-sm text-muted-foreground">
                  Active
                </div>
              </div>
              
              <div className="p-4 rounded-lg border bg-card">
                <div className={`text-2xl font-bold ${
                  metrics.running ? 'text-green-600' : 'text-red-600'
                }`}>
                  {metrics.running ? 'ON' : 'OFF'}
                </div>
                <div className="text-sm text-muted-foreground">
                  System
                </div>
              </div>
            </div>
          )}

          {/* Component Health */}
          {health?.components && (
            <div className="space-y-2">
              <h3 className="font-medium">Components</h3>
              <div className="space-y-2">
                {Object.entries(health.components).map(([name, component]) => (
                  <div key={name} className="flex items-center justify-between p-3 rounded-md border">
                    <div className="flex items-center gap-3">
                      {getStatusIcon(component.status)}
                      <div>
                        <div className="font-medium capitalize">
                          {name.replace(/_/g, ' ')}
                        </div>
                        <div className={`text-sm ${getStatusColor(component.status)}`}>
                          {component.status.toUpperCase()}
                        </div>
                      </div>
                    </div>
                    <div className="text-sm text-muted-foreground">
                      {new Date(component.last_update).toLocaleTimeString()}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}