import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthShell } from "@/components/auth-shell";
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
  afterEach(() => cleanup());

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
});
