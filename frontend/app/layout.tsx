import "./globals.css";

import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Inference Logger",
  description: "assistant-ui chatbot with inference ingestion",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}
