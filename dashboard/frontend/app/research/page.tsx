import PlaceholderPage from "@/components/PlaceholderPage";

export default function Page() {
  return (
    <PlaceholderPage
      title="Research"
      description="Papers indexed from research sources."
      endpoint={"GET /api/research-papers"}
    />
  );
}
