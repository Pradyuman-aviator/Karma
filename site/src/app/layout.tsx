import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";

import "./globals.css";

const description =
  "Karma runs only the pytest tests your change can break, down to the test function: static, explainable test selection, validated on real projects, with failure prediction, a flaky-test registry and failure diagnosis.";

export const metadata: Metadata = {
  metadataBase: new URL("https://pradyuman-aviator.github.io/Karma/"),
  title: "Karma: run only the tests your change can break",
  description,
  openGraph: {
    title: "Karma for pytest",
    description,
    images: [{ url: "og.png", width: 1200, height: 630 }],
    type: "website",
  },
  twitter: { card: "summary_large_image", title: "Karma for pytest", description },
  icons: { icon: "icon.svg" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f7f6" },
    { media: "(prefers-color-scheme: dark)", color: "#091216" },
  ],
};

// Before the first paint: the saved theme, else the system's, so nothing flashes.
const themeScript = `(function(){try{var t=localStorage.getItem("karma-theme");var d=t?t==="dark":matchMedia("(prefers-color-scheme: dark)").matches;document.documentElement.classList.toggle("dark",d)}catch(e){document.documentElement.classList.add("dark")}})()`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-dvh font-sans">{children}</body>
    </html>
  );
}
