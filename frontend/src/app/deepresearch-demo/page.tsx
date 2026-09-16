import { ResearchConversation } from "@/components/deepresearch/research-conversation";
import { QueryClientProvider } from "@/components/query-client-provider";
import { SidebarProvider } from "@/components/ui/sidebar";
import { AUTH_DISABLED_USER } from "@/core/auth/auth-disabled-user";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { I18nProvider } from "@/core/i18n/context";

export default function DeepResearchDemoPage() {
  return (
    <AuthProvider initialUser={AUTH_DISABLED_USER}>
      <I18nProvider initialLocale="zh-CN">
        <QueryClientProvider>
          <SidebarProvider>
            <ResearchConversation apiBase="http://127.0.0.1:8022" />
          </SidebarProvider>
        </QueryClientProvider>
      </I18nProvider>
    </AuthProvider>
  );
}
