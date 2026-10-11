"use client";

import { CopyButton } from "@/components/copy-button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function CodeTabs({
  tabs,
}: {
  tabs: { id: string; label: string; code: string; html: string }[];
}) {
  return (
    <Tabs defaultValue={tabs[0].id}>
      <TabsList>
        {tabs.map((t) => (
          <TabsTrigger key={t.id} value={t.id}>
            {t.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {tabs.map((t) => (
        <TabsContent key={t.id} value={t.id}>
          <div className="relative rounded-xl border bg-card">
            <CopyButton text={t.code} className="absolute right-3 top-3" />
            <div
              className="overflow-x-auto p-5 pr-14 [&_pre]:!bg-transparent"
              dangerouslySetInnerHTML={{ __html: t.html }}
            />
          </div>
        </TabsContent>
      ))}
    </Tabs>
  );
}
