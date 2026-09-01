import "./globals.css";
import { Shell } from "@/components/Shell";
import { TenantProvider } from "@/lib/useTenant";

export const metadata = {
  title: "Document intelligence cockpit",
  description: "Pipeline, search, ask and evaluation for the document intelligence MVP.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <TenantProvider>
          <Shell>{children}</Shell>
        </TenantProvider>
      </body>
    </html>
  );
}
