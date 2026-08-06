import { lazy, Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { App as AntApp, ConfigProvider, Skeleton } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import { AuthProvider, useAuth } from './hooks/useAuth';
import { ThemeProvider, useTheme } from './hooks/useTheme';
import MainLayout from './layouts/MainLayout';
import LoginPage from './pages/login/LoginPage';
import ChangePasswordPage from './pages/login/ChangePasswordPage';
import { pageRoles } from './config/navigation';
import { buildLoginUrl, getSafeReturnTo } from './utils/authNavigation.js';

dayjs.locale('zh-cn');
const antdZhCN = zhCN?.default || zhCN;

const CockpitPage = lazy(() => import('./pages/cockpit/CockpitPage'));
const SitesPage = lazy(() => import('./pages/sites/SitesPage'));
const AlertsPage = lazy(() => import('./pages/alerts/AlertsPage'));
const WorkOrdersPage = lazy(() => import('./pages/workorders/WorkOrdersPage'));
const ArchivePage = lazy(() => import('./pages/archive/ArchivePage'));
const EquipmentPage = lazy(() => import('./pages/equipment/EquipmentPage'));
const AnalysisPage = lazy(() => import('./pages/analysis/AnalysisPage'));
const UsersPage = lazy(() => import('./pages/users/UsersPage'));
const AuditPage = lazy(() => import('./pages/audit/AuditPage'));
const EvaluationPage = lazy(() => import('./pages/evaluation/EvaluationPage'));
const VehiclesPage = lazy(() => import('./pages/vehicles/VehiclesPage'));
const ReagentMasterPage = lazy(() => import('./pages/reagents/ReagentMasterPage'));
const PlanSchedulesPage = lazy(() => import('./pages/plan-schedules/PlanSchedulesPage'));
const ReportsPage = lazy(() => import('./pages/reports/ReportsPage'));
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'));

function RouteFallback() {
  return (
    <div style={{ padding: 24, width: '100%' }}>
      <Skeleton active title={{ width: 180 }} paragraph={{ rows: 5 }} />
    </div>
  );
}

function Deferred({ children }) {
  return <Suspense fallback={<RouteFallback />}>{children}</Suspense>;
}

function ProtectedRoute({ children, roles }) {
  const { isAuthenticated, user } = useAuth();
  const location = useLocation();
  if (!isAuthenticated) {
    const returnTo = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to={buildLoginUrl(returnTo)} replace />;
  }
  if (!user) return <RouteFallback />;
  if (user.must_change_password && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />;
  }
  if (roles?.length > 0 && !roles.some((role) => (user.roles || [user.role]).includes(role))) return <Navigate to="/" replace />;
  return children;
}

function ChangePasswordRoute() {
  const { isAuthenticated, user } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  if (!user) return <RouteFallback />;
  if (!user.must_change_password) return <Navigate to="/" replace />;
  return <ChangePasswordPage />;
}

function LoginRoute() {
  const { isAuthenticated, user } = useAuth();
  const location = useLocation();
  if (!isAuthenticated) return <LoginPage />;
  if (!user) return <RouteFallback />;
  if (user.must_change_password) {
    const returnTo = getSafeReturnTo(location.search);
    return <Navigate to={`/change-password?returnTo=${encodeURIComponent(returnTo)}`} replace />;
  }
  return <Navigate to={getSafeReturnTo(location.search)} replace />;
}

function PageRoute({ path, children }) {
  return <ProtectedRoute roles={pageRoles[path]}>{children}</ProtectedRoute>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route path="/change-password" element={<ChangePasswordRoute />} />
      <Route path="/" element={<ProtectedRoute><MainLayout /></ProtectedRoute>}>
        <Route index element={<Deferred><CockpitPage /></Deferred>} />
        <Route path="sites" element={(
          <PageRoute path="/sites"><Deferred><SitesPage /></Deferred></PageRoute>
        )} />
        <Route path="alerts" element={(
          <PageRoute path="/alerts"><Deferred><AlertsPage /></Deferred></PageRoute>
        )} />
        <Route path="workorders" element={(
          <PageRoute path="/workorders"><Deferred><WorkOrdersPage /></Deferred></PageRoute>
        )} />
        <Route path="plan-schedules" element={(
          <PageRoute path="/plan-schedules"><Deferred><PlanSchedulesPage /></Deferred></PageRoute>
        )} />
        <Route path="reports" element={(
          <PageRoute path="/reports"><Deferred><ReportsPage /></Deferred></PageRoute>
        )} />
        {/* 旧 inspection-v2 计划链路已停用；执行记录统一从计划调度详情查看。 */}
        <Route path="maintenance" element={<Navigate to="/plan-schedules" replace />} />
        <Route path="audit" element={(
          <PageRoute path="/audit"><Deferred><AuditPage /></Deferred></PageRoute>
        )} />
        <Route path="batch-review" element={<Navigate to="/audit?tab=photo" replace />} />
        <Route path="equipment" element={(
          <PageRoute path="/equipment"><Deferred><EquipmentPage /></Deferred></PageRoute>
        )} />
        <Route path="analysis" element={(
          <PageRoute path="/analysis"><Deferred><AnalysisPage /></Deferred></PageRoute>
        )} />
        <Route path="archive" element={(
          <PageRoute path="/archive"><Deferred><ArchivePage /></Deferred></PageRoute>
        )} />
        <Route path="users" element={(
          <PageRoute path="/users"><Deferred><UsersPage /></Deferred></PageRoute>
        )} />
        <Route path="vehicles" element={(
          <PageRoute path="/vehicles"><Deferred><VehiclesPage /></Deferred></PageRoute>
        )} />
        <Route path="reagents" element={(
          <PageRoute path="/reagents"><Deferred><ReagentMasterPage /></Deferred></PageRoute>
        )} />
        <Route path="evaluation" element={(
          <PageRoute path="/evaluation"><Deferred><EvaluationPage /></Deferred></PageRoute>
        )} />
        <Route path="*" element={<Deferred><NotFoundPage /></Deferred>} />
      </Route>
    </Routes>
  );
}

function ThemedApp() {
  const { themeConfig } = useTheme();
  return (
    <ConfigProvider theme={themeConfig} locale={antdZhCN}>
      <AntApp><AppRoutes /></AntApp>
    </ConfigProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ThemeProvider><ThemedApp /></ThemeProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
