import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, setCsrfToken } from "../api/client";
import type { Me, Permission } from "../api/types";

export function can(me: Me, permission: Permission) { return me.role === "admin" || me.permissions.includes(permission); }
export function canManage(me: Me) { return me.role === "admin" || me.permissions.length > 0; }

export const ME_KEY = ["me"] as const;

/** The signed-in user, or null when the session cookie is missing or expired. */
export function useMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: async (): Promise<Me | null> => {
      try {
        const me = await api.me();
        setCsrfToken(me.csrf_token);
        return me;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 30_000,
    // A task can finish while its conversation is not mounted.
    refetchInterval: query => query.state.data?.running_job_id ? 1500 : false,
  });
}

/** Store a freshly authenticated user (login or accepted invite). */
export function useSignedIn() {
  const client = useQueryClient();
  return (me: Me) => {
    setCsrfToken(me.csrf_token);
    // An old /me refresh must not replace the newly authenticated identity.
    void client.cancelQueries({ queryKey: ME_KEY });
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_KEY[0] });
    client.setQueryData(ME_KEY, me);
  };
}
