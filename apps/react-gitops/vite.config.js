import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // Куди складати зібрані файли. Саме цю теку Dockerfile копіює в nginx.
    outDir: "dist",
  },
});
