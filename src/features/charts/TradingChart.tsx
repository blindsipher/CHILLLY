import React, { useEffect, useRef } from 'react';
import { createChart, IChartApi, ISeriesApi, ColorType } from 'lightweight-charts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useMarketStore } from '@/stores/marketStore';
import { Bar } from '@/types';
import { BarChart3 } from 'lucide-react';

interface TradingChartProps {
  contract: string;
  height?: number;
}

export function TradingChart({ contract, height = 400 }: TradingChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const { bars, loadBars } = useMarketStore();

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#d1d5db',
      },
      grid: {
        vertLines: { color: '#374151' },
        horzLines: { color: '#374151' },
      },
      crosshair: {
        mode: 1,
      },
      rightPriceScale: {
        borderColor: '#374151',
        scaleMargins: {
          top: 0.1,
          bottom: 0.1,
        },
      },
      timeScale: {
        borderColor: '#374151',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
      },
      handleScale: {
        axisPressedMouseMove: true,
        mouseWheel: true,
        pinch: true,
      },
    });

    const candlestickSeries = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderDownColor: '#ef4444',
      borderUpColor: '#22c55e',
      wickDownColor: '#ef4444',
      wickUpColor: '#22c55e',
    });

    chartRef.current = chart;
    candlestickSeriesRef.current = candlestickSeries;

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
      }
    };
  }, [height]);

  // Load initial data
  useEffect(() => {
    if (contract) {
      loadBars(contract);
    }
  }, [contract, loadBars]);

  // Set initial chart data
  useEffect(() => {
    if (candlestickSeriesRef.current && contract) {
      const contractBars = bars.get(contract) || [];
      
      if (contractBars.length > 0) {
        const chartData = contractBars.map(bar => ({
          time: Math.floor(new Date(bar.timestamp).getTime() / 1000),
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        }));
        
        candlestickSeriesRef.current.setData(chartData);
      }
    }
  }, [bars, contract]);

  // Handle real-time updates
  useWebSocket(`market.bar.${contract}_1m`, (bar: Bar) => {
    if (candlestickSeriesRef.current) {
      candlestickSeriesRef.current.update({
        time: Math.floor(new Date(bar.timestamp).getTime() / 1000),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      });
    }
  }, [contract]);

  // Handle partial bar updates
  useWebSocket(`market.bar.${contract}_1m_partial`, (bar: Bar) => {
    if (candlestickSeriesRef.current) {
      candlestickSeriesRef.current.update({
        time: Math.floor(new Date(bar.timestamp).getTime() / 1000),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      });
    }
  }, [contract]);

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BarChart3 className="h-5 w-5" />
          {contract || 'Select Contract'}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div 
          ref={chartContainerRef} 
          className="w-full rounded-md border bg-card"
          style={{ height }}
        />
      </CardContent>
    </Card>
  );
}