import type { Metadata } from 'next';
import './globals.css';
import Script from 'next/script';

export const metadata: Metadata = {
  title: 'fpamed AI Toolkit',
  description: 'Custom fpamed AI powered workflows built on the Box platform.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link
          rel="stylesheet"
          href="https://cdn01.boxcdn.net/platform/elements/20.0.0/en-US/picker.css"
        />
      </head>
      <body className="bg-slate-50 text-slate-900 antialiased">
        {children}
        <Script
          src="https://cdn01.boxcdn.net/platform/elements/20.0.0/en-US/picker.js"
          strategy="beforeInteractive"
        />
      </body>
    </html>
  );
}
