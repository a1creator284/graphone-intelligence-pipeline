import PlaceholderPage from "@/components/PlaceholderPage";

export default function Page() {
  return (
    <PlaceholderPage
      title="Jobs"
      description="Open roles discovered across job boards."
      endpoint={"GET /api/jobs"}
    />
  );
}
