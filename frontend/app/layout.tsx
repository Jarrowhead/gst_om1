import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "GST Filing Platform",
  description: "GST returns automation for businesses and CA firms",
};

/**
 * HTML shell for every page. Sets the Inter font variable and full-height body.
 *
 * Flow:
 *   Render html + body around the matched route. No auth decision here.
 *
 * Debug:
 *   A blank page with the right URL is usually the child route, not this layout.
 */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}