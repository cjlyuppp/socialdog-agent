import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Page } from "../components/Layout";
import { ReferenceExplorer } from "../components/ReferenceExplorer";
import { Card, PageHeader } from "../components/ui";

export function ReferencePage() {
  const meta = useQuery({ queryKey: ["reference-meta"], queryFn: api.referenceMeta, staleTime: Infinity });
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
