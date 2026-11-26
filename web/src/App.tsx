import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import AppShell from './components/AppShell'
import ChatPage from './pages/ChatPage'
import DocumentsPage from './pages/DocumentsPage'
import SearchPage from './pages/SearchPage'
import StatusPage from './pages/StatusPage'
import AuditPage from './pages/AuditPage'
import LoginPage from './pages/LoginPage'
import { getAccessToken, getStoredUser, isAdmin, type AuthUser } from './lib/auth'
import type { ReactNode } from 'react'

/** 路由守卫：未登录一律回登录页；员工访问管理员页回问答页。 */
function RequireAuth({
  children,
  adminOnly = false,
  user,
}: {
  children: ReactNode
  adminOnly?: boolean
  user: AuthUser | null
}) {
  const location = useLocation()
  if (!getAccessToken() || !user) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }
  if (adminOnly && !isAdmin(user)) {
    return <Navigate to="/chat" replace />
  }
  return <>{children}</>
}

export default function App() {
  // 本地用户态只决定路由与导航显隐；合法性以后端 401 静默刷新为准
  const user = getStoredUser()
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AppShell user={user} />}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route
          path="/chat"
          element={
            <RequireAuth user={user}>
              <ChatPage />
            </RequireAuth>
          }
        />
        <Route
          path="/documents"
          element={
            <RequireAuth adminOnly user={user}>
              <DocumentsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/search"
          element={
            <RequireAuth adminOnly user={user}>
              <SearchPage />
            </RequireAuth>
          }
        />
        <Route
          path="/audit"
          element={
            <RequireAuth adminOnly user={user}>
              <AuditPage />
            </RequireAuth>
          }
        />
        <Route
          path="/status"
          element={
            <RequireAuth user={user}>
              <StatusPage />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  )
}
