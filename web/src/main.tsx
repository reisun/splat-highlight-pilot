import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { loadConfig } from "./config";
import "./styles/index.css";

loadConfig().then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}).catch((error: unknown) => {
  const root = document.getElementById("root")!;
  root.setAttribute("role", "alert");
  root.textContent = error instanceof Error ? error.message : "API 設定の読み込みに失敗しました。";
});
