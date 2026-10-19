import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Page } from "../components/Layout";
import { ReferenceExplorer } from "../components/ReferenceExplorer";
import { Card, PageHeader } from "../components/ui";
import { useMe } from "../lib/auth";

export function ReferencePage() {
  const me = useMe().data!;
  const meta = useQuery({ queryKey: ["reference-meta", me.id, me.role, "access-v3", {}], queryFn: () => api.referenceMeta() });
  return (
    <Page wide>
      <PageHeader
        title="数据核对"
        description={meta.data ? `${meta.data.observation_start} — ${meta.data.observation_end}` : undefined}
      />
      <Card className="p-5 md:p-6">
        <ReferenceExplorer />
      </Card>
    </Page>
  );
}
