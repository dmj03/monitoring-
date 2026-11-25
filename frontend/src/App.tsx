import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/ui";
import Alerts from "./pages/Alerts";
import AlertDetail from "./pages/AlertDetail";
import Clusters from "./pages/Clusters";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Navigate to="/alerts" replace />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/alerts/:alertId" element={<AlertDetail />} />
        <Route path="/clusters" element={<Clusters />} />
      </Routes>
    </Shell>
  );
}
