import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { persist } from 'zustand/middleware';
import { api } from '@/api/client';
import { wsManager } from '@/api/websocket';
import { Order, OrderRequest } from '@/types';

interface OrderState {
  orders: Map<string, Order>;
  isLoading: boolean;
  error: string | null;
  
  // Actions
  placeOrder: (order: OrderRequest) => Promise<void>;
  cancelOrder: (orderId: string) => Promise<void>;
  loadOrders: () => Promise<void>;
  updateOrder: (order: Order) => void;
  clearError: () => void;
}

export const useOrderStore = create<OrderState>()(
  persist(
    immer((set, get) => ({
      orders: new Map(),
      isLoading: false,
      error: null,

      placeOrder: async (orderRequest) => {
        set((state) => {
          state.isLoading = true;
          state.error = null;
        });

        try {
          await api.placeOrder(orderRequest);
          
          set((state) => {
            state.isLoading = false;
          });

          // Refresh orders to get the latest state
          await get().loadOrders();
        } catch (error) {
          set((state) => {
            state.error = error instanceof Error ? error.message : 'Failed to place order';
            state.isLoading = false;
          });
          throw error;
        }
      },

      cancelOrder: async (orderId) => {
        set((state) => {
          state.isLoading = true;
          state.error = null;
        });

        try {
          await api.cancelOrder(orderId);
          
          set((state) => {
            const order = state.orders.get(orderId);
            if (order) {
              order.status = 'CANCELED';
            }
            state.isLoading = false;
          });
        } catch (error) {
          set((state) => {
            state.error = error instanceof Error ? error.message : 'Failed to cancel order';
            state.isLoading = false;
          });
          throw error;
        }
      },

      loadOrders: async () => {
        set((state) => {
          state.isLoading = true;
          state.error = null;
        });

        try {
          const ordersData = await api.getOrders();
          
          set((state) => {
            // Convert object to Map if needed
            if (ordersData && typeof ordersData === 'object') {
              if (Array.isArray(ordersData)) {
                state.orders = new Map(ordersData.map(order => [order.id, order]));
              } else {
                // Handle object format from API
                state.orders = new Map(Object.entries(ordersData));
              }
            }
            state.isLoading = false;
          });
        } catch (error) {
          set((state) => {
            state.error = error instanceof Error ? error.message : 'Failed to load orders';
            state.isLoading = false;
          });
        }
      },

      updateOrder: (order) => {
        set((state) => {
          state.orders.set(order.id, order);
        });
      },

      clearError: () => {
        set((state) => {
          state.error = null;
        });
      },
    })),
    {
      name: 'order-store',
      partialize: (state) => ({ 
        orders: Array.from(state.orders.entries()) 
      }),
      onRehydrateStorage: () => (state) => {
        if (state && Array.isArray(state.orders)) {
          state.orders = new Map(state.orders);
        }
      },
    }
  )
);

// Setup WebSocket subscriptions for real-time order updates
if (typeof window !== 'undefined') {
  wsManager.subscribe('order.*', (payload: any) => {
    const { updateOrder } = useOrderStore.getState();
    if (payload && payload.order_id) {
      updateOrder(payload);
    }
  });
}

// Selectors
export const selectOpenOrders = (state: OrderState) =>
  Array.from(state.orders.values()).filter(order => 
    order.status === 'WORKING' || order.status === 'PARTIAL_FILL'
  );

export const selectOrdersByStrategy = (strategyId: string) => (state: OrderState) =>
  Array.from(state.orders.values()).filter(order => 
    order.strategy_id === strategyId
  );