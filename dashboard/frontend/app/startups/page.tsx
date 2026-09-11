import PlaceholderPage from "@/components/PlaceholderPage";

export default function Page() {
  return (
    <PlaceholderPage
      title="Startups"
      description="Companies ingested from startup directories."
      endpoint={"GET /api/startups"}
    />
  );
}
