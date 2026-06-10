import "./globals.css";
import Link from "next/link";
import type { ReactNode } from "react";

export const metadata = {
  title: "Pharma Pipeline Intelligence",
  description: "Pipeline intelligence dataset for biopharma investing",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav>
          <span className="brand">Pharma Pipeline Intelligence</span>
          <Link href="/">Explore</Link>
          <Link href="/companies">Companies</Link>
          <Link href="/review">Review</Link>
        </nav>
        <main>{children}</main>
      </body>
    </html>
  );
}
