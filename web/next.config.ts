import type { NextConfig } from "next";
import { backendRewrites } from "./lib/proxy";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return backendRewrites(process.env.API_UPSTREAM_URL);
  },
};

export default nextConfig;
