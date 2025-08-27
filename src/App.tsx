import React, { useEffect } from 'react';
import { LoginForm } from '@/components/LoginForm';
import { TradingDashboard } from '@/components/TradingDashboard';
import { useAuthStore } from '@/stores/authStore';

function App() {
  const { isAuthenticated, token } = useAuthStore();

  // Auto-reconnect WebSocket if we have a token
  useEffect(() => {
    if (token && !isAuthenticated) {
      // This handles the case where we have a persisted token but need to reconnect
      // The auth store will handle WebSocket reconnection
    }
  }, [token, isAuthenticated]);

  if (!isAuthenticated) {
    return <LoginForm />;
  }

  return <TradingDashboard />;
}

export default App;