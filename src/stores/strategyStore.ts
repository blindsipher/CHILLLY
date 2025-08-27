import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { api } from '@/api/client';
import { wsManager } from '@/api/websocket';
import { Strategy, StrategyConfig, StrategyPerformance } from '@/types';

interface StrategyState {
  strategies: Map<string, Strategy>;
  availableStrategies: any[];
  performance: Map<string, StrategyPerformance>;
  isLoading: boolean;
  error: string | null;

  // Actions
  loadStrategies: () => Promise<void>;
  loadAvailableStrategies: () => Promise<void>;
  addStrategy: (config: StrategyConfig) => Promise<void>;
  removeStrategy: (strategyId: string) => Promise<void>;
  updateStrategy: (strategyId: string, config: any) => Promise<void>;
  loadPerformance: (strategyId: string) => Promise<void>;
  updateStrategyStatus: (strategyId: string, status: Strategy['status']) => void;
  clearError: () => void;
}

export const useStrategyStore = create<StrategyState>()(
  immer((set, get) => ({
    strategies: new Map(),
    availableStrategies: [],
    performance: new Map(),
    isLoading: false,
    error: null,

    loadStrategies: async () => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
      });

      try {
        const strategies = await api.getStrategies();
        
        set((state) => {
          state.strategies = new Map(strategies.map(strategy => [strategy.id, strategy]));
          state.isLoading = false;
        });
      } catch (error) {
        set((state) => {
          state.error = error instanceof Error ? error.message : 'Failed to load strategies';
          state.isLoading = false;
        });
      }
    },

    loadAvailableStrategies: async () => {
      try {
        const available = await api.getAvailableStrategies();
        
        set((state) => {
          state.availableStrategies = available;
        });
      } catch (error) {
        console.error('Failed to load available strategies:', error);
      }
    },

    addStrategy: async (config) => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
      });

      try {
        await api.addStrategy(config);
        
        set((state) => {
          state.isLoading = false;
        });

        // Refresh strategies list
        await get().loadStrategies();
      } catch (error) {
        set((state) => {
          state.error = error instanceof Error ? error.message : 'Failed to add strategy';
          state.isLoading = false;
        });
        throw error;
      }
    },

    removeStrategy: async (strategyId) => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
      });

      try {
        await api.removeStrategy(strategyId);
        
        set((state) => {
          state.strategies.delete(strategyId);
          state.performance.delete(strategyId);
          state.isLoading = false;
        });
      } catch (error) {
        set((state) => {
          state.error = error instanceof Error ? error.message : 'Failed to remove strategy';
          state.isLoading = false;
        });
        throw error;
      }
    },

    updateStrategy: async (strategyId, config) => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
      });

      try {
        await api.updateStrategy(strategyId, config);
        
        set((state) => {
          state.isLoading = false;
        });

        // Refresh strategies list
        await get().loadStrategies();
      } catch (error) {
        set((state) => {
          state.error = error instanceof Error ? error.message : 'Failed to update strategy';
          state.isLoading = false;
        });
        throw error;
      }
    },

    loadPerformance: async (strategyId) => {
      try {
        const performance = await api.getStrategyPerformance(strategyId);
        
        set((state) => {
          state.performance.set(strategyId, performance);
        });
      } catch (error) {
        console.error(`Failed to load performance for ${strategyId}:`, error);
      }
    },

    updateStrategyStatus: (strategyId, status) => {
      set((state) => {
        const strategy = state.strategies.get(strategyId);
        if (strategy) {
          strategy.status = status;
        }
      });
    },

    clearError: () => {
      set((state) => {
        state.error = null;
      });
    },
  }))
);

// Setup WebSocket subscriptions for real-time strategy updates
if (typeof window !== 'undefined') {
  wsManager.subscribe('strategy.*', (payload: any) => {
    const { updateStrategyStatus } = useStrategyStore.getState();
    
    if (payload.strategy_id && payload.status) {
      updateStrategyStatus(payload.strategy_id, payload.status);
    }
  });
}