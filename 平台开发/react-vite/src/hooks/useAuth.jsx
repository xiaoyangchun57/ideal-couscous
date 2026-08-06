import { useState, useEffect, useCallback, createContext, useContext } from 'react';
import { api } from '../services/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => {
    try { return localStorage.getItem('water_ops_token') || ''; } catch { return ''; }
  });
  const [loading, setLoading] = useState(false);

  const applySession = useCallback((nextToken, nextUser) => {
    localStorage.setItem('water_ops_token', nextToken);
    setToken(nextToken);
    setUser(nextUser);
  }, []);

  const login = useCallback(async (username, password) => {
    setLoading(true);
    try {
      const res = await api.post('/auth/login', { username, password });
      if (res && res.token) {
        const nextUser = res.user || { username, role: res.role || 'user' };
        applySession(res.token, nextUser);
        return { success: true, mustChangePassword: !!nextUser.must_change_password };
      }
      return { success: false, error: res?.error || '登录失败' };
    } catch {
      return { success: false, error: '网络错误' };
    } finally {
      setLoading(false);
    }
  }, [applySession]);

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout', {});
    } finally {
      localStorage.removeItem('water_ops_token');
      setToken('');
      setUser(null);
    }
  }, []);

  const changePassword = useCallback(async (currentPassword, newPassword) => {
    setLoading(true);
    try {
      const res = await api.post('/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      });
      if (res?.token && res?.user) {
        applySession(res.token, res.user);
        return { success: true };
      }
      return { success: false, error: res?.error || '密码修改失败' };
    } finally {
      setLoading(false);
    }
  }, [applySession]);

  // Restore user on mount
  useEffect(() => {
    if (token && !user) {
      api.get('/auth/me').then(res => {
        if (res && res.user) setUser({ ...res.user, site_ids: res.site_ids ?? [] });
        else if (res && res.username) setUser(res);
      });
    }
  }, [token]);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, logout, changePassword, isAuthenticated: !!token }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
