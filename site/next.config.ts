import type { NextConfig } from "next";

// A fully static site: `next build` writes plain HTML to out/, which Vercel (or any
// static host) serves as is.
const config: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
};

export default config;
