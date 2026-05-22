import "./globals.css";
import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Inference Console",
  description: "Chatbot with inference logging",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">
        <header className="border-b border-ink-800/80 bg-ink-950/70 backdrop-blur sticky top-0 z-10">
          <div className="max-w-7xl mx-auto flex items-center justify-between px-6 py-3">
            <div className="flex items-center gap-2">
              <div className="size-2.5 rounded-full bg-accent shadow-[0_0_12px_2px_rgba(124,92,255,0.6)]" />
              <span className="font-semibold tracking-tight">Inference Console</span>
            </div>
            <nav className="flex items-center gap-1 text-sm">
              <Link href="/" className="px-3 py-1.5 rounded-md hover:bg-ink-800 transition">Chat</Link>
              <Link href="/dashboard" className="px-3 py-1.5 rounded-md hover:bg-ink-800 transition">Dashboard</Link>
              <Link href="/logs" className="px-3 py-1.5 rounded-md hover:bg-ink-800 transition">Logs</Link>
            </nav>
          </div>
        </header>
        <main className="max-w-7xl mx-auto px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
