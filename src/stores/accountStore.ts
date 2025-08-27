import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { api } from '@/api/client';
import { wsManager } from '@/api/websocket';
import { Account, Position, Trade } from '@/types';

interface AccountState {
  account: Account | null;
  positions: Position[];
  trades: Trade[];
  isLoading: boolean;
  error: string | null;

  // Actions
  loadAccount: () => Promise<void>;
  loadPositions: () => Promise<void>;
  loadTrades: () => Promise<void>;
  updatePosition: (position: Position) => void;
  addTrade: (trade: Trade) => void;
  clearError: () => void;
}

export const useAccountStore = create<AccountState>()(
  immer((set, get) => ({
    account: null,
    positions: [],
    trades: [],
    isLoading: false,
    error: null,

    loadAccount: async () => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
      });

      try {
        const account = await api.getAccount();
        
        set((state) => {
          state.account = account;
          state.isLoading = false;
        });
      } catch (error) {
        set((state) => {
          state.error = error instanceof Error ? error.message : 'Failed to load account';
          state.isLoading = false;
        });
      }
    },

    loadPositions: async () => {
      try {
        const positions = await api.getPositions();
        
        set((state) => {
          state.positions = positions;
        });
      } catch (error) {
        console.error('Failed to load positions:', error);
      }
    },

    loadTrades: async () => {
      try {
        const trades = await api.getTrades();
        
        set((state) => {
          state.trades = trades;
        });
      } catch (error) {
        console.error('Failed to load trades:', error);
      }
    },

    updatePosition: (position) => {
      set((state) => {
        const existingIndex = state.positions.findIndex(p => p.id === position.id);
        
        if (existingIndex >= 0) {
          state.positions[existingIndex] = position;
        } else {
          state.positions.push(position);
        }
      });
    },

    addTrade: (trade) => {
      set((state) => {
        state.trades.unshift(trade);
        // Keep only last 1000 trades
        if (state.trades.length > 1000) {
          state.trades = state.trades.slice(0, 1000);
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

// Setup WebSocket subscriptions for real-time account updates
if (typeof window !== 'undefined') {
  wsManager.subscribe('account.position.*', (payload: Position) => {
    const { updatePosition } = useAccountStore.getState();
    updatePosition(payload);
  });

  wsManager.subscribe('trade.*', (payload: Trade) => {
    const { addTrade } = useAccountStore.getState();
    addTrade(payload);
  });
}