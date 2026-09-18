import { ResearchSettings } from "@/components/deepresearch/research-settings";
import { QueryClientProvider } from "@/components/query-client-provider";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { AUTH_DISABLED_USER } from "@/core/auth/auth-disabled-user";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { I18nProvider } from "@/core/i18n/context";

export default function DeepResearchDemoSettingsPage() {
  return (
    <AuthProvider initialUser={AUTH_DISABLED_USER}>
      <I18nProvider initialLocale="zh-CN">
        <QueryClientProvider>
          <SidebarProvider className="h-screen">
            <SidebarInset className="min-w-0">
              <ResearchSettings
                apiBase="http://127.0.0.1:8022"
                backHref="/deepresearch-demo"
              />
            </SidebarInset>
          </SidebarProvider>
          <Toaster position="top-center" />
        </QueryClientProvider>
      </I18nProvider>
    </AuthProvider>
  );
}
