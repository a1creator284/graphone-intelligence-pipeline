import PlaceholderPage from "@/components/PlaceholderPage";

export default function Page() {
  return (
    <PlaceholderPage
      title="News"
      description="Articles collected within the freshness window."
      endpoint={"GET /api/news"}
    />
  );
}
