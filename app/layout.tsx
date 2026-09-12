import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Swarlink — Music, together in time",
  description: "A precision studio for clean music lessons, synchronized duets, and joinable remote concerts.",
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://swarlink.mhbhat.chatgpt.site"),
  openGraph: {
    title: "Swarlink — Music, together in time",
    description: "Clean the room. Align every phrase. Perform beyond distance.",
    type: "website",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "Swarlink — Music, together in time" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Swarlink — Music, together in time",
    description: "Clean the room. Align every phrase. Perform beyond distance.",
    images: ["/og.png"],
  },
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className={`${geistSans.variable} ${geistMono.variable}`}>{children}</body></html>;
}
