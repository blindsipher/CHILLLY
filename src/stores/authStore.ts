import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api } from '@/api/client';
import { wsManager } from '@/api/websocket';

interface AuthState {
  token: string | null;
  username: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;

  // Actions
  login: (username: string, apiKey: string) => Promise<void>;
  logout: () => void;
  clearError: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      username: null,
      isAuthenticated: false,
      isLoading: false,
      error: null,

      login: async (username: string, apiKey: string) => {
        set({ isLoading: true, error: null });

        try {
          const response = await api.authenticate(username, apiKey);
          const { token } = response;

          // Set token in API client
          api.setToken(token);

          // Connect WebSocket
          await wsManager.connect(token);

          set({
            token,
            username,
            isAuthenticated: true,
            isLoading: false,
            error: null,
          });
        } catch (error) {
          set({
            isLoading: false,
            error: error instanceof Error ? error.message : 'Authentication failed',
          });
          throw error;
        }
      },

      logout: () => {
        // Disconnect WebSocket
        wsManager.disconnect();

        // Clear API token
        api.setToken('');

        set({
          token: null,
          username: null,
          isAuthenticated: false,
          error: null,
        });
      },

      clearError: () => {
        set({ error: null });
      },
    }),
    {
      name: 'auth-store',
      partialize: (state) => ({
        token: state.token,
        username: state.username,
        isAuthenticated: state.isAuthenticated,
      }),
      onRehydrateStorage: () => (state) => {
        if (state?.token) {
          // Restore token in API client
          api.setToken(state.token);
          
          // Reconnect WebSocket
          wsManager.connect(state.token).catch(error => {
            console.error('Failed to reconnect WebSocket:', error);
          });
        }
      },
    }
  )
);