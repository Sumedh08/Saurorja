import { OrganizationScreen } from "@/components/organization-screen";

export default async function OrganizationPage({ params }: { params: Promise<{ organizationId: string }> }) {
  const { organizationId } = await params;
  return <OrganizationScreen organizationId={organizationId} />;
}
