export type Role = "OWNER" | "ADMIN" | "MANAGER" | "TECHNICIAN" | "VIEWER";
export type MemberStatus = "ACTIVE" | "SUSPENDED" | "REMOVED";

export type AuthState =
  | { state: "anonymous" }
  | { state: "authenticated" }
  | { state: "pending_identity"; invitation_available: boolean };

export type UserProfile = {
  id: string;
  display_name: string | null;
  email: string | null;
  email_verified: boolean;
};

export type Organization = {
  id: string;
  name: string;
  role?: Role;
  created_at?: string;
  updated_at?: string;
};

export type Membership = {
  membership_id: string;
  display_name: string | null;
  email?: string | null;
  role: Role;
  status: MemberStatus;
  created_at?: string;
  updated_at?: string;
  last_activated_at?: string | null;
  last_suspended_at?: string | null;
  removed_at?: string | null;
};

export type Invitation = {
  id: string;
  email: string;
  role: Exclude<Role, "OWNER">;
  status: "PENDING" | "ACCEPTED" | "REVOKED" | "EXPIRED";
  expires_at: string;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: { ...(init.headers ?? {}) },
  });
  if (!response.ok) {
    let payload: { error?: { code?: string; message?: string } } = {};
    try {
      payload = (await response.json()) as typeof payload;
    } catch {
      // Keep a safe generic message for non-JSON proxy failures.
    }
    throw new ApiError(
      payload.error?.message ?? "The request could not be completed.",
      response.status,
      payload.error?.code ?? "request_error",
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function csrfHeaders(token: string): HeadersInit {
  return { "Content-Type": "application/json", "X-CSRF-Token": token };
}
