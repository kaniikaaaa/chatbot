/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    return [
      { source: "/api/chat/:path*", destination: `${process.env.CHATBOT_API_URL || "http://chatbot-api:8000"}/:path*` },
      { source: "/api/ingest/:path*", destination: `${process.env.INGESTION_URL || "http://ingestion:8001"}/:path*` },
    ];
  },
};
export default nextConfig;
