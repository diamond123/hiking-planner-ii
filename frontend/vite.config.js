import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  return {
    define: {
      "import.meta.env.API_KEY": JSON.stringify(env.API_KEY || ""),
      "import.meta.env.API_URL": JSON.stringify(env.API_URL || ""),
      "import.meta.env.TURNSTILE_SITE_KEY": JSON.stringify(env.TURNSTILE_SITE_KEY || ""),
    },
  };
});
