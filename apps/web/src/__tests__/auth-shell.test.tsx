import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthShell, LogoutButton } from "@/components/auth-shell";
import { InvitationAcceptance } from "@/components/invitation-acceptance";
import { OrganizationScreen } from "@/components/organization-screen";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiOrigin = "http://localhost";

function renderWithQueries(element: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(<QueryClientProvider client={queryClient}>{element}</QueryClientProvider>);
}

function mockApi(dataForPath: (path: string) => unknown) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), apiOrigin).pathname;
    return Response.json(dataForPath(path));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Module 2 browser experience", () => {
  beforeEach(() => replace.mockReset());
  afterEach(() => {
    cleanup();
    document.querySelectorAll("body > form").forEach((form) => form.remove());
    vi.restoreAllMocks();
  });

  it("shows sign-in to anonymous visitors", async () => {
    mockApi((path) => {
      if (path === "/api/v1/auth/session") return { state: "anonymous" };
      throw new Error(`Unexpected request ${path}`);
    });
    renderWithQueries(<AuthShell />);
    expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/api/v1/auth/login",
    );
  });

  it("keeps a pending identity out of authenticated organization queries", async () => {
    const fetchMock = mockApi((path) => {
      if (path === "/api/v1/auth/session") {
        return { state: "pending_identity", invitation_available: false };
      }
      if (path === "/api/v1/auth/pending") return { invitation: null };
      throw new Error(`Unexpected request ${path}`);
    });
    renderWithQueries(<AuthShell />);
    expect(await screen.findByText("Your identity is authenticated, but Saurorja access requires an invitation. Ask an Organization Owner or installation operator.")).toBeInTheDocument();
    const paths = fetchMock.mock.calls.map(([input]) => new URL(String(input), apiOrigin).pathname);
    expect(paths).not.toContain("/api/v1/me");
    expect(paths).not.toContain("/api/v1/organizations");
  });

  it("shows the organization chooser for multiple active memberships", async () => {
    mockApi((path) => {
      if (path === "/api/v1/auth/session") return { state: "authenticated" };
      if (path === "/api/v1/me") {
        return { id: "user-1", display_name: "Ari", email: "ari@example.com", email_verified: true };
      }
      if (path === "/api/v1/organizations") {
        return {
          items: [
            { id: "org-1", name: "North Wind", role: "OWNER" },
            { id: "org-2", name: "South Wind", role: "VIEWER" },
          ],
          next_cursor: null,
        };
      }
      if (path === "/api/v1/auth/csrf") return { csrf_token: "test-csrf-token" };
      throw new Error(`Unexpected request ${path}`);
    });
    renderWithQueries(<AuthShell />);
    expect(await screen.findByText(/Signed in as Ari/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /North Wind/ })).toHaveAttribute(
      "href",
      "/organizations/org-1",
    );
    expect(screen.getByRole("link", { name: /South Wind/ })).toHaveAttribute(
      "href",
      "/organizations/org-2",
    );
  });

  it("does not expose member or invitation management to a Viewer", async () => {
    const fetchMock = mockApi((path) => {
      if (path === "/api/v1/auth/csrf") return { csrf_token: "test-csrf-token" };
      if (path === "/api/v1/organizations/org-1") {
        return { id: "org-1", name: "North Wind", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
      }
      if (path === "/api/v1/organizations/org-1/memberships/me") {
        return { membership_id: "membership-1", display_name: "Ari", email: null, role: "VIEWER", status: "ACTIVE" };
      }
      throw new Error(`Unexpected request ${path}`);
    });
    renderWithQueries(<OrganizationScreen organizationId="org-1" />);
    expect(await screen.findByText("Your membership")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Members" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Invitations" })).not.toBeInTheDocument();
    const paths = fetchMock.mock.calls.map(([input]) => new URL(String(input), apiOrigin).pathname);
    expect(paths).not.toContain("/api/v1/organizations/org-1/memberships");
    expect(paths).not.toContain("/api/v1/organizations/org-1/invitations");
  });

  it("accepts an invitation token from the URL fragment without retaining it in the URL", async () => {
    const token = "one-time-invitation-secret";
    window.history.replaceState(null, "", `/invitations/accept#t=${token}`);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), apiOrigin).pathname;
      if (path === "/api/v1/auth/session") {
        return Response.json({ state: "pending_identity", invitation_available: true });
      }
      if (path === "/api/v1/auth/csrf") return Response.json({ csrf_token: "pending-csrf" });
      if (path === "/api/v1/invitation-acceptance-attempts" && init?.method === "POST") {
        return Response.json({ id: "attempt-1", expires_at: "2026-01-01T00:00:00Z" });
      }
      if (path === "/api/v1/invitation-acceptance-attempts/attempt-1") {
        return Response.json({
          id: "attempt-1",
          organization: { id: "org-1", name: "North Wind" },
          email: "ari@example.com",
          role: "VIEWER",
          expires_at: "2026-01-01T00:00:00Z",
        });
      }
      if (path === "/api/v1/invitation-acceptance-attempts/attempt-1/accept") {
        return Response.json({ organization_id: "org-1" });
      }
      throw new Error(`Unexpected request ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithQueries(<InvitationAcceptance />);
    expect(await screen.findByRole("heading", { name: "Accept invitation" })).toBeInTheDocument();
    expect(window.location.hash).toBe("");
    const tokenPost = fetchMock.mock.calls.find(
      ([input, init]) =>
        new URL(String(input), apiOrigin).pathname === "/api/v1/invitation-acceptance-attempts" &&
        init?.method === "POST",
    );
    expect(tokenPost).toBeDefined();
    expect(JSON.parse(String(tokenPost?.[1]?.body))).toEqual({ token });
    expect(new URL(String(tokenPost?.[0]), apiOrigin).search).toBe("");

    fireEvent.click(screen.getByRole("button", { name: "Accept invitation" }));
    await waitFor(() => expect(replace).toHaveBeenLastCalledWith("/organizations/org-1"));
    const acceptPost = fetchMock.mock.calls.find(
      ([input]) =>
        new URL(String(input), apiOrigin).pathname ===
        "/api/v1/invitation-acceptance-attempts/attempt-1/accept",
    );
    expect(new Headers(acceptPost?.[1]?.headers).get("X-CSRF-Token")).toBe("pending-csrf");
  });

  it("submits logout with the session-bound CSRF token", async () => {
    mockApi((path) => {
      if (path === "/api/v1/auth/csrf") return { csrf_token: "logout-csrf" };
      throw new Error(`Unexpected request ${path}`);
    });
    const submit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => undefined);
    renderWithQueries(<LogoutButton />);

    await waitFor(() => expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    const form = document.querySelector<HTMLFormElement>('form[action="/api/v1/auth/logout"]');
    expect(form?.method).toBe("post");
    expect(form?.querySelector<HTMLInputElement>('input[name="csrf_token"]')?.value).toBe("logout-csrf");
  });

  it("lets a Manager view members without administration controls", async () => {
    mockApi((path) => organizationResponse(path, "MANAGER"));
    renderWithQueries(<OrganizationScreen organizationId="org-1" />);
    expect(await screen.findByText("Viewer")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Members" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Invitations" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Organization name")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Role for Viewer")).not.toBeInTheDocument();
  });

  it("lets an Admin manage non-Owners but not Owner memberships", async () => {
    mockApi((path) => organizationResponse(path, "ADMIN"));
    renderWithQueries(<OrganizationScreen organizationId="org-1" />);
    expect(await screen.findByRole("heading", { name: "Invitations" })).toBeInTheDocument();
    expect(screen.getByLabelText("Organization name")).toBeInTheDocument();
    expect(screen.queryByLabelText("Role for Owner")).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Role for Viewer")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Role for Viewer")).getByRole("option", { name: "ADMIN" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Role")).queryByRole("option", { name: "OWNER" })).not.toBeInTheDocument();
  });

  it("lets an Owner administer other Owners while invitations cannot grant OWNER", async () => {
    mockApi((path) => organizationResponse(path, "OWNER"));
    renderWithQueries(<OrganizationScreen organizationId="org-1" />);
    expect(await screen.findByLabelText("Role for Owner")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Invitations" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Role")).queryByRole("option", { name: "OWNER" })).not.toBeInTheDocument();
  });
});

function organizationResponse(path: string, role: "OWNER" | "ADMIN" | "MANAGER") {
  if (path === "/api/v1/auth/csrf") return { csrf_token: "test-csrf-token" };
  if (path === "/api/v1/organizations/org-1") {
    return { id: "org-1", name: "North Wind", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
  }
  if (path === "/api/v1/organizations/org-1/memberships/me") {
    return { membership_id: "actor-membership", display_name: "Ari", email: null, role, status: "ACTIVE" };
  }
  if (path === "/api/v1/organizations/org-1/memberships") {
    return { items: [
      { membership_id: "owner-membership", display_name: "Owner", email: null, role: "OWNER", status: "ACTIVE" },
      { membership_id: "viewer-membership", display_name: "Viewer", email: null, role: "VIEWER", status: "ACTIVE" },
    ] };
  }
  if (path === "/api/v1/organizations/org-1/invitations") return { items: [] };
  throw new Error(`Unexpected request ${path}`);
}
