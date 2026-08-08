import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * El build sale a `dist/`, que es lo que `firebase.json` declara como `public`.
 *
 * En desarrollo, `/api` se reenvía al backend local. En producción ese mismo
 * prefijo lo resuelve el rewrite de Firebase Hosting hacia Cloud Run, así que el
 * código del cliente usa rutas relativas y no conoce ninguna URL de backend:
 * una URL cableada funciona en una máquina y falla en las otras dos.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    // Sin sourcemaps en producción: exponen el código original de una consola
    // que muestra datos de activos y nombres de acciones del dominio.
    sourcemap: false,
  },
});
