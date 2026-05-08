import { useEffect, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

function formatDate(iso: string): string {
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}/${m}/${day}`;
}

export default function Header() {
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    const uiBuildDate = __BUILD_DATE__;
    const api = API_BASE_URL || window.location.origin;

    fetch(`${api}/health`)
      .then((r) => r.json())
      .then((data) => {
        const apiDate: string | undefined = data.updated_at;
        if (apiDate && apiDate > uiBuildDate) {
          setUpdatedAt(formatDate(apiDate));
        } else {
          setUpdatedAt(formatDate(uiBuildDate));
        }
      })
      .catch(() => {
        setUpdatedAt(formatDate(uiBuildDate));
      });
  }, []);

  return (
    <header className="bg-gray-900 text-white py-4 px-6 shadow-md flex items-center justify-between">
      <h1 className="text-xl font-bold tracking-wide">
        Splat Highlight Pilot
      </h1>
      {updatedAt && (
        <span className="text-xs text-gray-400">Updated: {updatedAt}</span>
      )}
    </header>
  );
}
