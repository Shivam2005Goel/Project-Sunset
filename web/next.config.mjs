/** @type {import('next').NextConfig} */
const nextConfig = {
  // Cloud Run wants a self-contained server bundle, not a node_modules tree.
  output: "standalone",
  env: {
    // The dashboard is a thin client over services/api. Same origin in cloud, :8000 in dev.
    AFTERCARE_API: process.env.AFTERCARE_API ?? "http://127.0.0.1:8000",
  },
};

export default nextConfig;
