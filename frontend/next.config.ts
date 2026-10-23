import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  typescript: {
    // The production server has too little memory for the build's own type check (it crashed with "out of memory").
    // The server build sets NEXT_SKIP_TYPECHECK=1; type checking still happens in CI (npx tsc --noEmit) and everywhere else.
    ignoreBuildErrors: process.env.NEXT_SKIP_TYPECHECK === "1",
  },
};

export default nextConfig;
