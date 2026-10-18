/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Always inline the demo flag (even when unset) so that, with demo mode off, the bundler folds the
  // `=== "1"` guard in lib/signer.tsx to false and drops the chunk holding the public Anvil test keys.
  env: { NEXT_PUBLIC_DEMO_MODE: process.env.NEXT_PUBLIC_DEMO_MODE ?? "0" },
  // wagmi pulls optional connector deps we never use; keep the bundler quiet about them.
  webpack: (config) => {
    config.externals.push("pino-pretty", "lokijs", "encoding");
    config.resolve.fallback = { ...config.resolve.fallback, "@react-native-async-storage/async-storage": false };
    return config;
  },
};
export default nextConfig;
