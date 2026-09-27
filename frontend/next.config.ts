import type { NextConfig } from "next";

const BACKEND = "http://127.0.0.1:8084";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    return [
      {
        // Same-origin API proxy: the httpOnly refresh cookie (path /api/v1/auth)
        // flows without CORS, and no bearer token ever sits in localStorage.
        source: "/api/v1/:path*",
        destination: `${BACKEND}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;