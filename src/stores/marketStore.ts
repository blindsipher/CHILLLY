import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { api } from '@/api/client';
import { wsManager } from '@/api/websocket';
import { Contract, Bar, Quote } from '@/types';

interface MarketState {
  contracts: Contract[];
  quotes: Map<string, Quote>;
  bars: Map<string, Bar[]>;
  selectedContract: string | null;
  isLoading: boolean;
  error: string | null;

  // Actions
  loadContracts: () => Promise<void>;
  loadBars: (contractId: string) => Promise<void>;
  updateQuote: (quote: Quote) => void;
  updateBar: (bar: Bar) => void;
  setSelectedContract: (contractId: string) => void;
  clearError: () => void;
}

export const useMarketStore = create<MarketState>()(
  immer((set, get) => ({
    contracts: [],
    quotes: new Map(),
    bars: new Map(),
    selectedContract: null,
    isLoading: false,
    error: null,

    loadContracts: async () => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
      });

      try {
        const contracts = await api.getContracts();
        
        set((state) => {
          state.contracts = contracts;
          state.isLoading = false;
        });
      } catch (error) {
        set((state) => {
          state.error = error instanceof Error ? error.message : 'Failed to load contracts';
          state.isLoading = false;
        });
      }
    },

    loadBars: async (contractId: string) => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
      });

      try {
        const bars = await api.getBars(contractId);
        
        set((state) => {
          state.bars.set(contractId, bars);
          state.isLoading = false;
        });
      } catch (error) {
        set((state) => {
          state.error = error instanceof Error ? error.message : 'Failed to load bars';
          state.isLoading = false;
        });
      }
    },

    updateQuote: (quote) => {
      set((state) => {
        state.quotes.set(quote.contract_id, quote);
      });
    },

    updateBar: (bar) => {
      set((state) => {
        const contractBars = state.bars.get(bar.contract_id) || [];
        
        // Update existing bar or add new one
        const existingIndex = contractBars.findIndex(
          b => b.timestamp === bar.timestamp && b.timeframe === bar.timeframe
        );
        
        if (existingIndex >= 0) {
          contractBars[existingIndex] = bar;
        } else {
          contractBars.push(bar);
          // Keep only last 1000 bars
          if (contractBars.length > 1000) {
            contractBars.splice(0, contractBars.length - 1000);
          }
        }
        
        state.bars.set(bar.contract_id, contractBars);
      });
    },

    setSelectedContract: (contractId) => {
      set((state) => {
        state.selectedContract = contractId;
      });
    },

    clearError: () => {
      set((state) => {
        state.error = null;
      });
    },
  }))
);

// Setup WebSocket subscriptions for real-time market data
if (typeof window !== 'undefined') {
  wsManager.subscribe('market.quote.*', (payload: Quote) => {
    const { updateQuote } = useMarketStore.getState();
    updateQuote(payload);
  });

  wsManager.subscribe('market.bar.*', (payload: Bar) => {
    const { updateBar } = useMarketStore.getState();
    updateBar(payload);
  });
}