import { lazy, Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { App as AntApp, ConfigProvider, Skeleton } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { AuthProvider, useAuth } from './hooks/useAuth';
import { ThemeProvider, useTheme } from './hooks/useTheme';
import MainLayout from './layouts/MainLayout';
import LoginPage from './pages/login/LoginPage';

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
const ReportsPage = lazy(() => import('./pages/reports/ReportsPage'));
const ReagentMasterPage = lazy(() => import('./pages/reagents/ReagentMasterPage'));
const PlanSchedulesPage = lazy(() => import('./pages/plan-schedules/PlanSchedulesPage'));
const BatchReviewPage = lazy(() => import('./pages/batch-review/BatchReviewPage'));

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
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  if (!user) return <RouteFallback />;
  if (roles?.length > 0 && !roles.includes(user.role)) return <Navigate to="/" replace />;
  return children;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<ProtectedRoute><MainLayout /></ProtectedRoute>}>
        <Route index element={<Deferred><CockpitPage /></Deferred>} />
        <Route path="sites" element={<Deferred><SitesPage /></Deferred>} />
        <Route path="alerts" element={<Deferred><AlertsPage /></Deferred>} />
        <Route path="workorders" element={<Deferred><WorkOrdersPage /></Deferred>} />
        <Route path="plan-schedules" element={<Deferred><PlanSchedulesPage /></Deferred>} />
        {/* 旧 inspection-v2 计划链路已停用；执行记录统一从计划调度详情查看。 */}
        <Route path="maintenance" element={<Navigate to="/plan-schedules" replace />} />
        <Route path="audit" element={(
          <ProtectedRoute roles={['admin', 'manager', 'reviewer', 'inspector']}><Deferred><AuditPage /></Deferred></ProtectedRoute>
        )} />
        <Route path="batch-review" element={(
          <ProtectedRoute roles={['admin', 'manager', 'reviewer', 'inspector']}><Deferred><BatchReviewPage /></Deferred></ProtectedRoute>
        )} />
        <Route path="equipment" element={<Deferred><EquipmentPage /></Deferred>} />
        <Route path="analysis" element={<Deferred><AnalysisPage /></Deferred>} />
        <Route path="archive" element={<Deferred><ArchivePage /></Deferred>} />
        <Route path="users" element={(
          <ProtectedRoute roles={['admin']}><Deferred><UsersPage /></Deferred></ProtectedRoute>
        )} />
        <Route path="vehicles" element={<Deferred><VehiclesPage /></Deferred>} />
        <Route path="reagents" element={<Deferred><ReagentMasterPage /></Deferred>} />
        <Route path="evaluation" element={(
          <ProtectedRoute roles={['admin', 'manager', 'reviewer', 'inspector']}><Deferred><EvaluationPage /></Deferred></ProtectedRoute>
        )} />
        <Route path="reports" element={<Deferred><ReportsPage /></Deferred>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function ThemedApp() {
  const { themeConfig } = useTheme();
  return (
    <ConfigProvider theme={themeConfig} locale={zhCN}>
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
