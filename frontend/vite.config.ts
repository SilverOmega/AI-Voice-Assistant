/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const target = (name: string, port: number) =>
  process.env[`${name.toUpperCase()}_SERVICE_URL`] ?? `http://localhost:${port}`;

const service = (prefix: string, port: number) => ({
  target: target(prefix, port),
  changeOrigin: true,
  ws: true,
  rewrite: (path: string) => path.replace(new RegExp(`^/${prefix}`), ""),
});

// The browser only ever talks to this origin; /chat, /voice and /wakeword are proxied
// to the three backend services (the production nginx config does the same).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/chat": service("chat", 8001),
      "/voice": service("voice", 8002),
      "/wakeword": service("wakeword", 8003),
    },
  },
  test: { environment: "node" },
});
