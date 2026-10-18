import type { Metadata, Viewport } from "next";
import { Inter, Space_Grotesk } from "next/font/google";
import "./globals.css";
import { Background } from "@/components/Background";
import { Header } from "@/components/Header";
import { Providers } from "./providers";

const inter = Inter({ subsets: ["latin", "latin-ext"], weight: ["400", "700"], variable: "--font-inter", display: "swap" });
const space = Space_Grotesk({ subsets: ["latin", "latin-ext"], weight: ["700"], variable: "--font-space", display: "swap" });

export const metadata: Metadata = {
  title: "EmbedProof: tamper-evident, post-quantum-signed CO₂ certificates",
  description:
    "A steel mill's CO₂-per-tonne claim becomes an attestor-signed certificate, checked against the plant's own history, and an on-chain ledger cannot allocate the same tonnes twice. Hackathon prototype.",
};

export const viewport: Viewport = { themeColor: "#0B1020", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${space.variable}`}>
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <Background />
        <Providers>
          <Header />
          <main id="main" className="mx-auto w-full max-w-6xl px-4 pb-24 pt-8 sm:px-6 sm:pt-12">
            {children}
          </main>
          <footer className="mx-auto max-w-6xl px-4 pb-10 text-center text-xs text-white/70 sm:px-6">
            Hackathon prototype · not accredited CBAM verification · fictional companies and demo data only · ML-DSA-44 (FIPS 204) + ECDSA secp256k1
          </footer>
        </Providers>
      </body>
    </html>
  );
}
