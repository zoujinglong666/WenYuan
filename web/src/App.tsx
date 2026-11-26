import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import ChatPage from './pages/ChatPage'
import DocumentsPage from './pages/DocumentsPage'
import SearchPage from './pages/SearchPage'
import StatusPage from './pages/StatusPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/status" element={<StatusPage />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  )
}
