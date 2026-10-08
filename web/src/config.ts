let apiBaseUrl = "";

export function getApiBaseUrl(): string {
  return apiBaseUrl;
}

export async function loadConfig(): Promise<void> {
  try {
    const response = await fetch(`${import.meta.env.BASE_URL}config.json`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const config: unknown = await response.json();
    const value = (config as { apiBaseUrl?: unknown } | null)?.apiBaseUrl;
    if (typeof value !== "string" || !value) throw new Error("apiBaseUrl is missing");
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
        url.pathname !== "/" || url.search || url.hash ||
        (import.meta.env.PROD && url.protocol !== "https:")) {
      throw new Error("apiBaseUrl must be an HTTPS origin");
    }
    apiBaseUrl = url.origin;
  } catch (error) {
    if (import.meta.env.DEV) {
      apiBaseUrl = import.meta.env.VITE_API_BASE_URL || window.location.origin;
      return;
    }
    throw new Error(`API 接続設定を読み込めません。config.json を確認してください。${String(error)}`);
  }
}
