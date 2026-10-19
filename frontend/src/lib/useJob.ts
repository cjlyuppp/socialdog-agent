import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

/** Poll a job until it finishes. Swap for SSE once the backend offers it. */
export function useJob(jobId: string | null | undefined) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "ok" || status === "error" ? false : 700;
    },
  });
}
